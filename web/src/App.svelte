<script>
  import { onDestroy } from 'svelte';
  import { Connection } from './lib/connection.js';
  import { getNick, getToken } from './lib/identity.js';
  import { DEFAULTS, applyPrefs, loadPrefs, savePrefs } from './lib/prefs.js';
  import Lobby from './components/Lobby.svelte';
  import Table from './components/Table.svelte';
  import WakeScreen from './components/WakeScreen.svelte';

  // The table link is the table: ?t=<id>. A rejoin link adds &r=<secret>.
  const params = new URLSearchParams(location.search);
  const tableId = params.get('t');
  const rejoin = params.get('r');

  let conn = $state(null);
  let view = $state(null);
  let prefs = $state(loadPrefs());
  let unsubscribe = null;

  function connect(nick) {
    conn = new Connection({ tableId, nick, rejoin });
    unsubscribe = conn.subscribe((value) => (view = value));
    conn.start();
    if (rejoin) {
      // Do not leave a secret sitting in the address bar or the history.
      params.delete('r');
      history.replaceState({}, '', `${location.pathname}?${params}`);
    }
  }

  // A saved token, or a rejoin link in the address, means we already know
  // who this is: connect without asking again.
  if (tableId && (getToken(tableId) || rejoin)) connect(getNick() || 'Player');

  // Keep the document in step with the look-and-feel preferences, now and
  // whenever they change.
  $effect(() => {
    applyPrefs(prefs);
  });

  function updatePrefs(patch) {
    prefs = { ...prefs, ...patch };
    savePrefs(prefs);
  }

  // Phones freeze a backgrounded tab. Coming back, re-check the socket and
  // pull a fresh state rather than trusting what is on screen.
  $effect(() => {
    if (!conn) return;
    const resume = () => {
      if (document.visibilityState === 'visible') conn.resume();
    };
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('online', resume);
    return () => {
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('online', resume);
    };
  });

  // Keep the screen awake while sitting at a table (section 8.4). Best
  // effort: not every browser offers it.
  $effect(() => {
    if (view?.status !== 'open' || !('wakeLock' in navigator)) return;
    let lock = null;
    let cancelled = false;
    const take = async () => {
      try {
        lock = await navigator.wakeLock.request('screen');
      } catch {
        /* denied or unsupported */
      }
    };
    take();
    const retake = () => {
      if (!cancelled && document.visibilityState === 'visible') take();
    };
    document.addEventListener('visibilitychange', retake);
    return () => {
      cancelled = true;
      document.removeEventListener('visibilitychange', retake);
      lock?.release?.().catch(() => {});
    };
  });

  function dismissRejoin() {
    conn.set({ rejoinLink: null });
  }

  function rejoinUrl(secret) {
    return `${location.origin}${location.pathname}?t=${encodeURIComponent(tableId)}&r=${encodeURIComponent(secret)}`;
  }

  onDestroy(() => {
    unsubscribe?.();
    conn?.stop();
  });
</script>

{#if !conn}
  <Lobby {tableId} onJoin={connect} />
{:else if view.status === 'error'}
  <div class="message-screen">
    <h2>That table could not be opened</h2>
    <p>{view.error}</p>
    <button onclick={() => location.reload()}>Try again</button>
  </div>
{:else if view.status === 'replaced'}
  <div class="message-screen">
    <h2>Open on another device</h2>
    <p>{view.notice}</p>
    <button onclick={() => location.reload()}>Play here instead</button>
  </div>
{:else if view.state == null}
  <WakeScreen
    startedAt={view.wakeStartedAt}
    status={view.status}
    diagnosis={view.diagnosis}
  />
{:else}
  <Table
    view={view.state}
    status={view.status}
    error={view.error}
    busy={view.pending > 0}
    {prefs}
    conn={conn}
    onPrefs={updatePrefs}
    hands={view.hands}
    replay={view.replay}
    historyLoading={view.historyLoading}
  />
{/if}

{#if view?.rejoinLink}
  <div class="modal" role="dialog" aria-label="your rejoin link">
    <h2>Save your rejoin link</h2>
    <p>
      This is how you get back to your seat on another phone, or if this
      browser forgets you. Treat it like a password — anyone who has it can
      play as you.
    </p>
    <input readonly value={rejoinUrl(view.rejoinLink)} onclick={(e) => e.currentTarget.select()} />
    <div class="buttons">
      <button
        onclick={() => navigator.clipboard?.writeText(rejoinUrl(view.rejoinLink))}
      >
        Copy
      </button>
      <button class="primary" onclick={dismissRejoin}>Saved it</button>
    </div>
  </div>
{/if}

<style>
  .message-screen {
    min-height: 100dvh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    padding: 24px;
    text-align: center;
  }

  h2 {
    margin: 0;
    font-size: 20px;
  }

  p {
    margin: 0;
    max-width: 40ch;
    color: var(--text-dim);
    line-height: 1.5;
  }

  .modal {
    position: fixed;
    z-index: 40;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    width: min(440px, calc(100vw - 32px));
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 20px;
    background: var(--bg-raised);
    border: 1px solid var(--accent);
    border-radius: var(--radius);
    box-shadow: 0 30px 80px rgba(0, 0, 0, 0.6);
  }

  .modal input {
    font-family: var(--numerals);
    font-size: 12px;
  }

  .buttons {
    display: flex;
    gap: 8px;
  }

  .buttons button {
    flex: 1;
  }

  .primary {
    background: var(--accent);
    border-color: transparent;
    color: #1a1020;
    font-weight: 650;
  }
</style>
