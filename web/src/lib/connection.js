/**
 * The live connection to the table.
 *
 * This is the piece that makes a free Render service feel like a server that
 * is always there:
 *
 *   waking       the page is up (GitHub Pages never sleeps) but the backend
 *                may not be, so we poll /healthz until it answers ready
 *   connecting   opening the WebSocket
 *   open         playing; a heartbeat keeps Render from idling out
 *   reconnecting backing off after a drop, then trying again
 *
 * Actions are queued with their action id and re-sent after a reconnect. The
 * server recognises the id and ignores the replay, so "did my fold land?" is
 * never a question the player has to answer.
 *
 * Everything external (fetch, WebSocket, timers) is injected so this file can
 * be tested in plain Node.
 */

import {
  BACKEND_URL,
  PROTOCOL_VERSION,
  configProblem,
  healthUrl,
  pageOrigin,
  wsUrl,
} from '../config.js';
import { clearToken, getToken, newActionId, saveToken } from './identity.js';

const BACKOFF_MS = [500, 1000, 2000, 4000, 8000, 15000, 30000];
const WAKE_POLL_MS = 2000;
const WAKE_TIMEOUT_MS = 5000;
const DEFAULT_HEARTBEAT_MS = 120000;
// How many health checks must fail before we stop blaming a sleeping
// server and say we cannot reach it. A server that is merely starting
// still *answers* (Render holds the request), so a total non-answer is
// already a strong signal; three is enough to rule out a blip.
const UNREACHABLE_BEFORE_DIAGNOSIS = 3;
// If the socket is open but no snapshot has arrived, ask again rather than
// sitting on an empty screen.
const STATE_WAIT_MS = 4000;

// Errors that mean "stop trying": retrying cannot help and the player needs
// to see the reason.
const TERMINAL_CODES = new Set([
  'no_table',
  'protocol_mismatch',
  'stale_state',
  'no_database',
  'join_failed',
  'bad_rejoin',
  'no_such_player',
]);

export class Connection {
  /**
   * @param {object} options
   * @param {string} options.tableId
   * @param {string} options.nick
   * @param {boolean} [options.spectate]
   * @param {object} [options.deps] injected for tests
   */
  constructor({ tableId, nick = 'Player', spectate = false, rejoin = null, deps = {} }) {
    this.tableId = tableId;
    this.nick = nick;
    this.spectate = spectate;
    // A rejoin link from the URL, used once on the first hello.
    this.rejoin = rejoin;

    this.fetch = deps.fetch ?? globalThis.fetch?.bind(globalThis);
    this.WebSocketImpl = deps.WebSocket ?? globalThis.WebSocket;
    this.setTimeout = deps.setTimeout ?? globalThis.setTimeout.bind(globalThis);
    this.clearTimeout = deps.clearTimeout ?? globalThis.clearTimeout.bind(globalThis);
    this.now = deps.now ?? (() => Date.now());
    this.healthUrl = deps.healthUrl ?? healthUrl;
    this.wsUrl = deps.wsUrl ?? wsUrl;
    this.jitter = deps.jitter ?? (() => 0.8 + Math.random() * 0.4);

    this.socket = null;
    this.timer = null;
    this.heartbeatTimer = null;
    this.attempt = 0;
    this.heartbeatMs = DEFAULT_HEARTBEAT_MS;
    this.stopped = false;
    /** Consecutive health checks that got no answer at all. */
    this.unreachableFor = 0;
    this.stateWatchdog = null;
    /** Actions sent but not yet acknowledged, keyed by action id. */
    this.outbox = new Map();
    this.subscribers = new Set();

    this.value = {
      status: 'idle',
      state: null,
      playerId: null,
      token: getToken(tableId),
      error: null,
      notice: null,
      // A rejoin link the server has just issued, shown once and then
      // dismissed. It is never part of a state snapshot.
      rejoinLink: null,
      // Whether the backend answered the last health check, and what is
      // probably wrong when it has not answered for a while.
      reachable: true,
      diagnosis: null,
      // Hand history. A read rather than an action: the list and the
      // open replay are answers to a request, not part of table state.
      hands: [],
      replay: null,
      historyLoading: false,
      wakeStartedAt: null,
      attempt: 0,
      pending: 0,
    };
  }

  // ---------------------------------------------------------- store contract

  subscribe(run) {
    this.subscribers.add(run);
    run(this.value);
    return () => this.subscribers.delete(run);
  }

  set(patch) {
    this.value = { ...this.value, ...patch, pending: this.outbox.size };
    for (const run of this.subscribers) run(this.value);
  }

  // ------------------------------------------------------------- lifecycle

  start() {
    this.stopped = false;
    this.wake();
  }

  stop() {
    this.stopped = true;
    this.clearTimer();
    this.stopHeartbeat();
    this.clearStateWatchdog();
    if (this.socket) {
      this.socket.onclose = null;
      try {
        this.socket.close();
      } catch {
        /* already gone */
      }
      this.socket = null;
    }
  }

  clearTimer() {
    if (this.timer) {
      this.clearTimeout(this.timer);
      this.timer = null;
    }
  }

  // ------------------------------------------------------------- waking up

  async wake() {
    if (this.stopped) return;
    this.set({
      status: 'waking',
      error: null,
      wakeStartedAt: this.value.wakeStartedAt ?? this.now(),
    });

    let ready = false;
    // Three outcomes, not two, and the difference is the whole diagnosis:
    //   reached   the server answered (it may still be starting up)
    //   timedOut  we gave up waiting — which is what a cold start looks
    //             like, so it is not evidence of anything being wrong
    //   neither   the request failed outright: wrong address, server down,
    //             or the browser refused to show us the answer
    let reached = false;
    let timedOut = false;
    try {
      const controller = new AbortController();
      const timeout = this.setTimeout(() => controller.abort(), WAKE_TIMEOUT_MS);
      const response = await this.fetch(this.healthUrl(), {
        signal: controller.signal,
        cache: 'no-store',
      });
      this.clearTimeout(timeout);
      reached = true;
      if (response.ok) {
        const body = await response.json();
        this.heartbeatMs = (body.heartbeat_seconds ?? 120) * 1000;
        ready = body.ready === true;
      }
    } catch (error) {
      ready = false;
      reached = false;
      timedOut = error?.name === 'AbortError';
    }

    if (this.stopped) return;

    // A timeout is the normal shape of a free instance starting up, so it
    // does not count against the server.
    this.unreachableFor = reached || timedOut ? 0 : this.unreachableFor + 1;

    let diagnosis = null;
    if (!reached && !timedOut) {
      // Provably wrong from this device, whatever the server is doing.
      diagnosis = configProblem();

      if (!diagnosis && this.unreachableFor >= UNREACHABLE_BEFORE_DIAGNOSIS) {
        // The request failed, but did it fail to *arrive*? A no-cors probe
        // is sent the same way and is not subject to the permission check,
        // so if that one gets through, the server is alive and well and
        // simply has not been told to accept this page. That is a one-line
        // fix on the server, and worth naming precisely.
        diagnosis = (await this.serverRefusesUs())
          ? {
              title: 'The server will not accept this page',
              detail:
                `${BACKEND_URL} is running, but it is not allowing requests ` +
                `from ${pageOrigin()}. On the server, set ALLOWED_ORIGIN to ` +
                `exactly ${pageOrigin()} — no path, no trailing slash — and ` +
                'restart it.',
            }
          : {
              title: 'Cannot reach the server',
              // A blocked request and a dead server are identical from
              // here — the browser reports both as a bare failure. An ad
              // blocker or privacy shield is the more common of the two
              // by far, and the quickest to rule out, so it goes first.
              detail:
                `Nothing is answering at ${BACKEND_URL}. If the table works ` +
                'in another browser, a content blocker or privacy shield in ' +
                'this one is blocking it — switch it off for this site. ' +
                'Otherwise check the server is running.',
            };
      }
    }
    this.set({ reachable: reached, diagnosis });

    if (ready) {
      this.set({ wakeStartedAt: null, diagnosis: null });
      this.connect();
    } else {
      this.timer = this.setTimeout(() => this.wake(), WAKE_POLL_MS);
    }
  }

  /**
   * Did the last request fail to arrive, or merely fail to be shown to us?
   *
   * A browser blocks a cross-origin response the page has no permission to
   * read, and reports it to the page exactly like a network failure — same
   * error, no detail, by design. The request itself went out and was
   * answered perfectly well.
   *
   * A `no-cors` request is not subject to that permission check: the
   * response comes back opaque and unreadable, but it comes back. So if
   * this one succeeds where the real one failed, the server is up and the
   * problem is that it has not been told to accept this page.
   */
  async serverRefusesUs() {
    try {
      const controller = new AbortController();
      const timeout = this.setTimeout(() => controller.abort(), WAKE_TIMEOUT_MS);
      await this.fetch(this.healthUrl(), {
        mode: 'no-cors',
        cache: 'no-store',
        signal: controller.signal,
      });
      this.clearTimeout(timeout);
      return true;
    } catch {
      return false;
    }
  }

  // ------------------------------------------------------------ connecting

  connect() {
    if (this.stopped) return;
    this.set({ status: this.attempt === 0 ? 'connecting' : 'reconnecting' });

    let socket;
    try {
      socket = new this.WebSocketImpl(this.wsUrl(this.tableId));
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.set({ status: 'open', error: null, attempt: 0 });
      this.sendRaw({
        t: 'hello',
        protocol: PROTOCOL_VERSION,
        nick: this.nick,
        token: this.value.token,
        rejoin: this.rejoin,
        spectate: this.spectate,
      });
      // A rejoin link is good for one move to this device; after that the
      // device's own token takes over.
      this.rejoin = null;
      this.flushOutbox();
      this.startHeartbeat();
      this.watchForState();
    };

    socket.onmessage = (event) => this.receive(event.data);

    socket.onerror = () => {
      /* onclose always follows; handled there */
    };

    socket.onclose = () => {
      this.stopHeartbeat();
      if (this.socket === socket) this.socket = null;
      this.scheduleReconnect();
    };
  }

  scheduleReconnect() {
    if (this.stopped) return;
    this.attempt += 1;

    // Several failures in a row usually means the service has gone back to
    // sleep, so return to the wake screen rather than hammering the socket.
    if (this.attempt >= 4) {
      this.attempt = 0;
      this.set({ status: 'waking', wakeStartedAt: this.now() });
      this.timer = this.setTimeout(() => this.wake(), WAKE_POLL_MS);
      return;
    }

    const base = BACKOFF_MS[Math.min(this.attempt - 1, BACKOFF_MS.length - 1)];
    const delay = Math.round(base * this.jitter());
    this.set({ status: 'reconnecting', attempt: this.attempt });
    this.timer = this.setTimeout(() => this.connect(), delay);
  }

  // ------------------------------------------------------------- heartbeat

  startHeartbeat() {
    this.stopHeartbeat();
    const beat = () => {
      this.sendRaw({ t: 'ping', at: this.now() });
      this.heartbeatTimer = this.setTimeout(beat, this.heartbeatMs);
    };
    this.heartbeatTimer = this.setTimeout(beat, this.heartbeatMs);
  }

  /**
   * Make sure a snapshot actually arrives after connecting.
   *
   * The welcome and the first state are two separate messages, and the
   * table is unusable without the second. If it goes missing — a send that
   * failed, a broadcast that did not reach us — nothing else would ever ask
   * for it, and the page would sit on the waking screen while the server
   * happily considers us joined. So: ask again, and keep asking.
   */
  watchForState() {
    this.clearStateWatchdog();
    const check = () => {
      if (this.stopped || !this.isOpen()) return;
      if (this.value.state != null) return;
      this.sendRaw({ t: 'resync' });
      this.stateWatchdog = this.setTimeout(check, STATE_WAIT_MS);
    };
    this.stateWatchdog = this.setTimeout(check, STATE_WAIT_MS);
  }

  clearStateWatchdog() {
    if (this.stateWatchdog) {
      this.clearTimeout(this.stateWatchdog);
      this.stateWatchdog = null;
    }
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) {
      this.clearTimeout(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  /**
   * Call when the tab comes back to the foreground. Phones freeze timers in
   * the background, so the socket may be dead without us having noticed.
   */
  resume() {
    if (this.stopped) return;
    if (this.isOpen()) {
      this.sendRaw({ t: 'ping', at: this.now() });
      this.sendRaw({ t: 'resync' });
    } else {
      this.clearTimer();
      this.attempt = 0;
      this.connect();
    }
  }

  isOpen() {
    return this.socket != null && this.socket.readyState === 1;
  }

  // -------------------------------------------------------------- messages

  receive(raw) {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }

    switch (msg.t) {
      case 'welcome': {
        if (msg.token) {
          saveToken(this.tableId, msg.token);
          this.set({ token: msg.token });
        }
        this.set({ playerId: msg.player_id, notice: null });
        if (msg.heartbeat) this.heartbeatMs = msg.heartbeat * 1000;
        break;
      }
      case 'state':
        this.clearStateWatchdog();
        this.set({ state: msg.state, error: null, diagnosis: null });
        break;
      case 'ack':
        this.outbox.delete(msg.aid);
        this.set({});
        break;
      case 'pong':
        break;
      case 'rejoin_link':
        this.set({ rejoinLink: msg.rejoin });
        break;
      case 'claim_pending':
        this.set({ notice: msg.message });
        break;
      case 'history':
        this.set({ hands: msg.hands ?? [], replay: null, historyLoading: false });
        break;
      case 'replay':
        this.set({ replay: msg.hand ?? null, historyLoading: false });
        break;
      case 'replaced':
        // Someone opened this table as the same player elsewhere.
        this.stop();
        this.set({
          status: 'replaced',
          notice: 'You opened this table on another device. This one is now idle.',
        });
        break;
      case 'error':
        if (msg.aid) this.outbox.delete(msg.aid);
        if (TERMINAL_CODES.has(msg.code)) {
          this.stop();
          this.set({ status: 'error', error: msg.message });
        } else {
          this.set({ error: msg.message });
        }
        break;
      default:
        break;
    }
  }

  sendRaw(message) {
    if (!this.isOpen()) return false;
    try {
      this.socket.send(JSON.stringify(message));
      return true;
    } catch {
      return false;
    }
  }

  /** Queue an action. Safe to call while offline. */
  send(kind, data = {}) {
    const aid = newActionId();
    const message = { t: 'action', aid, kind, data };
    this.outbox.set(aid, message);
    this.sendRaw(message);
    this.set({});
    return aid;
  }

  /**
   * Ask for the hand history: the list with no argument, one hand with
   * `{hand_no}`. It changes nothing, so it carries no action id and is
   * not queued for replay after a reconnect — if the socket is down, the
   * panel simply asks again when it comes back.
   */
  send_history({ hand_no = null, before = null, limit = 20 } = {}) {
    this.set({ historyLoading: true, ...(hand_no == null ? { replay: null } : {}) });
    const message = { t: 'history', limit };
    if (hand_no != null) message.hand_no = hand_no;
    if (before != null) message.before = before;
    if (!this.sendRaw(message)) this.set({ historyLoading: false });
  }

  flushOutbox() {
    for (const message of this.outbox.values()) this.sendRaw(message);
  }

  /** Forget this device's claim on the player (used by "leave" in phase 1). */
  forgetMe() {
    clearToken(this.tableId);
    this.set({ token: null });
  }
}
