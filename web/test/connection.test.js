/**
 * The client half of "survives sleep and restarts".
 *
 * Timers, fetch and WebSocket are all injected, so a reconnect that would
 * take half a minute in a browser takes no time here.
 *
 * Run with: npm test   (inside web/)
 */

import assert from 'node:assert/strict';
import test from 'node:test';
import { Connection } from '../src/lib/connection.js';

/** A clock the test drives by hand. */
class Clock {
  constructor() {
    this.now = 0;
    this.queue = new Map();
    this.nextId = 1;
  }

  setTimeout = (fn, ms) => {
    const id = this.nextId++;
    this.queue.set(id, { fn, at: this.now + (ms ?? 0) });
    return id;
  };

  clearTimeout = (id) => this.queue.delete(id);

  /** Run everything due within `ms`, in time order. */
  async advance(ms) {
    const until = this.now + ms;
    for (let guard = 0; guard < 1000; guard += 1) {
      const due = [...this.queue.entries()]
        .filter(([, t]) => t.at <= until)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      const [id, timer] = due;
      this.queue.delete(id);
      this.now = Math.max(this.now, timer.at);
      timer.fn();
      await Promise.resolve();
      await Promise.resolve();
    }
    this.now = until;
  }
}

class FakeSocket {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    FakeSocket.instances.push(this);
  }

  send(data) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  deliver(message) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  drop() {
    this.readyState = 3;
    this.onclose?.({});
  }

  get messages() {
    return this.sent;
  }
}

function setup({ ready = true, readyAfter = 0 } = {}) {
  FakeSocket.instances = [];
  const clock = new Clock();
  let healthCalls = 0;

  const conn = new Connection({
    tableId: 't1',
    nick: 'Ana',
    deps: {
      WebSocket: FakeSocket,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
      now: () => clock.now,
      healthUrl: () => 'http://backend/healthz',
      wsUrl: (id) => `ws://backend/ws?table=${id}`,
      jitter: () => 1,
      fetch: async () => {
        healthCalls += 1;
        return {
          ok: true,
          json: async () => ({
            ready: ready && healthCalls > readyAfter,
            heartbeat_seconds: 120,
          }),
        };
      },
    },
  });

  const seen = [];
  conn.subscribe((value) => seen.push(value.status));
  return { conn, clock, seen, health: () => healthCalls };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('waits for the server to wake, then connects', async () => {
  const { conn, clock, health } = setup({ readyAfter: 2 });
  conn.start();
  await settle();

  assert.equal(conn.value.status, 'waking');
  assert.equal(FakeSocket.instances.length, 0, 'no socket while still waking');

  await clock.advance(2000);
  await settle();
  await clock.advance(2000);
  await settle();

  assert.ok(health() >= 3, 'kept polling health');
  assert.equal(FakeSocket.instances.length, 1, 'connected once ready');
  conn.stop();
});

test('says hello with the saved token and starts the heartbeat', async () => {
  const { conn, clock } = setup();
  conn.start();
  await settle();
  const socket = FakeSocket.instances[0];
  socket.open();

  const hello = socket.messages[0];
  assert.equal(hello.t, 'hello');
  assert.equal(hello.nick, 'Ana');
  assert.equal(conn.value.status, 'open');

  socket.deliver({ t: 'welcome', player_id: 'p1', token: 'tok', heartbeat: 120 });
  socket.deliver({ t: 'state', seq: 1, state: { counter: 0 } });
  assert.equal(conn.value.playerId, 'p1');
  assert.deepEqual(conn.value.state, { counter: 0 });

  await clock.advance(121000);
  assert.ok(
    socket.messages.some((m) => m.t === 'ping'),
    'heartbeat keeps the free service awake',
  );
  conn.stop();
});

test('an action sent while offline is re-sent with the same id', async () => {
  const { conn, clock } = setup();
  conn.start();
  await settle();
  const first = FakeSocket.instances[0];
  first.open();
  first.deliver({ t: 'welcome', player_id: 'p1', token: 'tok' });

  first.drop();
  const aid = conn.send('bump');
  assert.equal(conn.value.pending, 1);

  await clock.advance(1000);
  await settle();
  const second = FakeSocket.instances[1];
  assert.ok(second, 'reconnected');
  second.open();

  const replayed = second.messages.find((m) => m.t === 'action');
  assert.equal(replayed.aid, aid, 'same action id, so the server can ignore a double');

  // The server recognises the replay; the client stops holding it.
  second.deliver({ t: 'ack', aid, duplicate: true });
  assert.equal(conn.value.pending, 0);
  conn.stop();
});

test('backs off, then falls back to the wake screen', async () => {
  const { conn, clock } = setup();
  conn.start();
  await settle();

  FakeSocket.instances[0].open();
  FakeSocket.instances[0].drop();
  assert.equal(conn.value.status, 'reconnecting');

  // Three failed attempts in a row, each dropping immediately.
  for (let i = 1; i <= 3; i += 1) {
    await clock.advance(31000);
    await settle();
    FakeSocket.instances.at(-1)?.drop();
  }

  assert.equal(
    conn.value.status,
    'waking',
    'after several failures, assume the service went back to sleep',
  );
  conn.stop();
});

test('a terminal error stops the retrying and shows the reason', async () => {
  const { conn, clock } = setup();
  conn.start();
  await settle();
  const socket = FakeSocket.instances[0];
  socket.open();

  socket.deliver({
    t: 'error',
    code: 'no_table',
    message: 'No table with that link.',
    aid: null,
  });

  assert.equal(conn.value.status, 'error');
  assert.equal(conn.value.error, 'No table with that link.');

  await clock.advance(60000);
  assert.equal(FakeSocket.instances.length, 1, 'did not keep retrying');
});

test('a rejected action is shown but does not stop play', async () => {
  const { conn } = setup();
  conn.start();
  await settle();
  const socket = FakeSocket.instances[0];
  socket.open();

  const aid = conn.send('say', { text: '' });
  socket.deliver({ t: 'error', code: 'rejected', message: 'That message was empty.', aid });

  assert.equal(conn.value.status, 'open');
  assert.equal(conn.value.error, 'That message was empty.');
  assert.equal(conn.value.pending, 0, 'the rejected action is not retried forever');
  conn.stop();
});

test('being opened on another device parks this one', async () => {
  const { conn, clock } = setup();
  conn.start();
  await settle();
  const socket = FakeSocket.instances[0];
  socket.open();

  socket.deliver({ t: 'replaced' });

  assert.equal(conn.value.status, 'replaced');
  await clock.advance(60000);
  assert.equal(FakeSocket.instances.length, 1, 'does not fight the other device');
});

test('coming back to the foreground asks for a fresh state', async () => {
  const { conn } = setup();
  conn.start();
  await settle();
  const socket = FakeSocket.instances[0];
  socket.open();
  socket.sent.length = 0;

  conn.resume();

  assert.deepEqual(
    socket.messages.map((m) => m.t),
    ['ping', 'resync'],
  );
  conn.stop();
});

test('resuming on a dead socket reconnects at once', async () => {
  const { conn } = setup();
  conn.start();
  await settle();
  FakeSocket.instances[0].open();
  FakeSocket.instances[0].drop();

  conn.resume();

  assert.equal(FakeSocket.instances.length, 2, 'no waiting out the backoff');
  conn.stop();
});

test('a snapshot that never arrives is asked for again', async () => {
  // The welcome lands but the state does not — a send that failed, or a
  // broadcast that missed us. Nothing else would ever ask, so the page
  // would sit on the waking screen while the server thinks we are in.
  const { conn, clock } = setup();
  conn.start();
  await settle();
  const socket = FakeSocket.instances[0];
  socket.open();
  socket.deliver({ t: 'welcome', player_id: 'p1', token: 'tok' });
  socket.sent.length = 0;

  await clock.advance(4500);

  assert.ok(
    socket.messages.some((m) => m.t === 'resync'),
    'it asks for the state again',
  );

  // And once it arrives, it stops asking.
  socket.deliver({ t: 'state', seq: 1, state: { counter: 0 } });
  socket.sent.length = 0;
  await clock.advance(20000);
  assert.equal(
    socket.messages.filter((m) => m.t === 'resync').length,
    0,
    'no more nagging once the table is on screen',
  );
  conn.stop();
});

test('an unreachable backend is eventually explained', async () => {
  const clock = new Clock();
  FakeSocket.instances = [];
  const conn = new Connection({
    tableId: 't1',
    nick: 'Ana',
    deps: {
      WebSocket: FakeSocket,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
      now: () => clock.now,
      healthUrl: () => 'http://backend/healthz',
      wsUrl: (id) => `ws://backend/ws?table=${id}`,
      jitter: () => 1,
      fetch: async () => {
        throw new Error('ERR_CONNECTION_REFUSED');
      },
    },
  });
  conn.subscribe(() => {});
  conn.start();
  await settle();

  assert.equal(conn.value.diagnosis, null, 'one failure is not a verdict');

  for (let i = 0; i < 4; i += 1) {
    await clock.advance(2500);
    await settle();
  }

  assert.ok(conn.value.diagnosis, 'after several failures it says so');
  assert.match(conn.value.diagnosis.title, /reach/i);
  assert.equal(conn.value.reachable, false);
  conn.stop();
});

test('a slow server is not mistaken for a missing one', async () => {
  // A free instance takes about a minute to start, and our own timeout
  // fires long before that. Giving up waiting must never be reported as
  // "cannot reach the server" — that sends people hunting for a fault
  // that is not there.
  const clock = new Clock();
  FakeSocket.instances = [];
  const conn = new Connection({
    tableId: 't1',
    nick: 'Ana',
    deps: {
      WebSocket: FakeSocket,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
      now: () => clock.now,
      healthUrl: () => 'http://backend/healthz',
      wsUrl: (id) => `ws://backend/ws?table=${id}`,
      jitter: () => 1,
      fetch: async () => {
        const error = new Error('aborted');
        error.name = 'AbortError';
        throw error;
      },
    },
  });
  conn.subscribe(() => {});
  conn.start();
  await settle();
  for (let i = 0; i < 6; i += 1) {
    await clock.advance(2500);
    await settle();
  }

  assert.equal(conn.value.diagnosis, null, 'still believed to be starting');
  assert.equal(conn.value.status, 'waking');
  conn.stop();
});

test('a server that refuses this page is named as such', async () => {
  // The plain request fails the way a blocked one does, but the no-cors
  // probe gets through: the server is alive and simply has not been told
  // to accept us.
  const clock = new Clock();
  FakeSocket.instances = [];
  const conn = new Connection({
    tableId: 't1',
    nick: 'Ana',
    deps: {
      WebSocket: FakeSocket,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
      now: () => clock.now,
      healthUrl: () => 'http://backend/healthz',
      wsUrl: (id) => `ws://backend/ws?table=${id}`,
      jitter: () => 1,
      fetch: async (_url, options = {}) => {
        if (options.mode === 'no-cors') return { ok: false, type: 'opaque' };
        throw new TypeError('Failed to fetch');
      },
    },
  });
  conn.subscribe(() => {});
  conn.start();
  await settle();
  for (let i = 0; i < 4; i += 1) {
    await clock.advance(2500);
    await settle();
  }

  assert.ok(conn.value.diagnosis, 'it has a verdict');
  assert.match(conn.value.diagnosis.title, /will not accept/i);
  assert.match(conn.value.diagnosis.detail, /ALLOWED_ORIGIN/);
  conn.stop();
});
