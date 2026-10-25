<script>
  /**
   * No table in the link yet: make one, or type a nickname and sit down.
   * Phase 2 replaces this with the real create/join flow (seat count, blinds,
   * buy-in), but the shape — link in, nickname, go — stays.
   */
  import { SITE_NAME, BACKEND_URL } from '../config.js';
  import { getNick, saveNick } from '../lib/identity.js';

  let { tableId = null, onJoin } = $props();

  let nick = $state(getNick());
  let busy = $state(false);
  let error = $state(null);

  async function createTable() {
    busy = true;
    error = null;
    try {
      const response = await fetch(`${BACKEND_URL}/api/tables`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Home game' }),
      });
      if (!response.ok) throw new Error('The server would not make a table.');
      const { table_id: id } = await response.json();
      // The link is the table: putting it in the URL makes it shareable and
      // makes a refresh land back in the same place.
      location.search = `?t=${encodeURIComponent(id)}`;
    } catch (err) {
      error = err.message ?? 'Could not reach the server.';
      busy = false;
    }
  }

  function join(event) {
    event.preventDefault();
    const name = nick.trim();
    if (!name) return;
    saveNick(name);
    onJoin(name);
  }
</script>

<div class="screen">
  <h1>{SITE_NAME}</h1>

  {#if tableId}
    <p class="lede">You have been invited to a table.</p>
    <form onsubmit={join}>
      <label for="nick">Your name at the table</label>
      <input
        id="nick"
        bind:value={nick}
        maxlength="20"
        autocomplete="nickname"
        placeholder="e.g. Ana"
      />
      <button class="primary" type="submit" disabled={!nick.trim()}>Sit down</button>
    </form>
    <p class="hint">
      Your seat is remembered on this device. Phase 1 adds a personal rejoin
      link for switching phones.
    </p>
  {:else}
    <p class="lede">A private, play-money table for your home game.</p>
    <button class="primary" onclick={createTable} disabled={busy}>
      {busy ? 'Making a table…' : 'Start a table'}
    </button>
    <p class="hint">
      You will get a link to share. Anyone with the link can join; there are no
      accounts and no passwords.
    </p>
  {/if}

  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}
</div>

<style>
  .screen {
    min-height: 100dvh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 16px;
    padding: 24px;
    text-align: center;
  }

  h1 {
    margin: 0;
    font-size: 30px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--accent);
    font-weight: 600;
  }

  .lede {
    margin: 0;
    color: var(--text-dim);
    max-width: 32ch;
    line-height: 1.5;
  }

  form {
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: min(320px, 100%);
    text-align: left;
  }

  label {
    font-size: 13px;
    color: var(--text-dim);
  }

  .primary {
    background: var(--accent);
    border-color: transparent;
    color: #1a1020;
    font-weight: 650;
    width: min(320px, 100%);
  }

  .hint {
    margin: 0;
    max-width: 34ch;
    font-size: 13px;
    color: var(--text-dim);
    line-height: 1.5;
  }

  .error {
    color: var(--red);
    margin: 0;
  }
</style>
