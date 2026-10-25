<script>
  /**
   * Host and co-host controls. Everything structural takes effect from the
   * next hand, which the panel says plainly so nobody expects a blind
   * change to land mid-hand.
   *
   * The server checks every one of these again, so a stray value here is
   * refused with a reason rather than quietly applied.
   */
  let { view, open = false, conn, onClose } = $props();

  let presetName = $state('');

  let settings = $state({});
  let editing = $state(null);
  let editAmount = $state(0);
  let editReason = $state('');
  let confirmEnd = $state(false);

  // Start from the table's current settings each time the panel opens.
  $effect(() => {
    if (open) settings = { ...view.settings };
  });

  let changed = $derived(
    Object.fromEntries(
      Object.entries(settings).filter(([key, value]) => view.settings[key] !== value),
    ),
  );

  function apply() {
    if (!Object.keys(changed).length) return;
    conn.send('settings', { changes: changed });
  }

  function saveStack() {
    conn.send('edit_stack', {
      player_id: editing,
      amount: Number(editAmount),
      reason: editReason,
    });
    editing = null;
    editReason = '';
  }

  const NUMBERS = [
    ['small_blind', 'Small blind'],
    ['big_blind', 'Big blind'],
    ['ante', 'Ante'],
    ['preflop_limit', 'Preflop limit (0 = off)'],
    ['max_straddle', 'Max straddle (0 = off)'],
    ['max_runs', 'Max runs'],
    ['buy_in', 'Default buy-in'],
    ['min_buy_in', 'Smallest buy-in'],
    ['max_buy_in', 'Largest buy-in'],
    ['fixed_buy_in', 'Fixed buy-in (0 = off)'],
    ['seat_count', 'Seats'],
    ['action_seconds', 'Action clock (seconds)'],
    ['time_bank_seconds', 'Time bank (seconds)'],
    ['showdown_seconds', 'Show/muck time (seconds)'],
    ['rathole_minutes', 'Rathole minutes (0 = off)'],
  ];
</script>

{#if open}
  <div
    class="scrim"
    role="button"
    tabindex="-1"
    aria-label="close host controls"
    onclick={onClose}
    onkeydown={(e) => e.key === 'Escape' && onClose()}
  ></div>
  <section class="panel" aria-label="host controls">
    <header>
      <h2>Host controls</h2>
      <button class="close" onclick={onClose} aria-label="close">&times;</button>
    </header>

    <div class="body">
      {#if view.pending_buy_ins?.length}
        <section>
          <h3>Waiting to buy in</h3>
          {#each view.pending_buy_ins as request (request.id)}
            <div class="request">
              <span>{request.nick} &middot; seat {request.seat + 1} &middot; {request.amount}</span>
              <button
                onclick={() =>
                  conn.send('resolve_buy_in', { request: request.id, approve: true })}
              >
                Approve
              </button>
              <button
                class="deny"
                onclick={() =>
                  conn.send('resolve_buy_in', { request: request.id, approve: false })}
              >
                Deny
              </button>
            </div>
          {/each}
        </section>
      {/if}

      {#if view.pending_claims?.length}
        <section>
          <h3>Is this really them?</h3>
          <p class="note">
            Someone on a new device says they are an existing player. Only
            approve this if you have asked them.
          </p>
          {#each view.pending_claims as claim (claim.id)}
            <div class="request">
              <span>{claim.nick}</span>
              <button
                onclick={() =>
                  conn.send('resolve_claim', { claim: claim.id, approve: true })}
              >
                It's them
              </button>
              <button
                class="deny"
                onclick={() =>
                  conn.send('resolve_claim', { claim: claim.id, approve: false })}
              >
                No
              </button>
            </div>
          {/each}
        </section>
      {/if}

      <section>
        <h3>Table</h3>
        <p class="note">These take effect from the next hand.</p>

        <label class="row">
          <span>Betting structure</span>
          <select bind:value={settings.structure}>
            <option value="no-limit">No limit</option>
            <option value="pot-limit">Pot limit</option>
          </select>
        </label>

        <label class="row">
          <span>Ante type</span>
          <select bind:value={settings.ante_type}>
            <option value="none">None</option>
            <option value="all">Everyone antes</option>
            <option value="big-blind">Big blind antes for all</option>
          </select>
        </label>

        <label class="row">
          <span>Coming back after a cash-out</span>
          <select bind:value={settings.resume_after_cash_out}>
            <option value="allowed">Allowed</option>
            <option value="approval">Needs approval</option>
            <option value="off">Off</option>
          </select>
        </label>

        {#each NUMBERS as [key, label] (key)}
          <label class="row">
            <span>{label}</span>
            <input type="number" min="0" bind:value={settings[key]} />
          </label>
        {/each}

        <label class="row check">
          <input type="checkbox" bind:checked={settings.rabbit_hunting} />
          <span>Rabbit hunting</span>
        </label>
        <label class="row check">
          <input type="checkbox" bind:checked={settings.auto_approve_buy_ins} />
          <span>Approve buy-ins automatically</span>
        </label>

        <button class="primary" disabled={!Object.keys(changed).length} onclick={apply}>
          {Object.keys(changed).length
            ? `Apply ${Object.keys(changed).length} change${Object.keys(changed).length > 1 ? 's' : ''}`
            : 'No changes'}
        </button>
      </section>

      <section>
        <h3>Presets</h3>
        <p class="muted">
          Keep this table's whole setup under a name and load it next time.
        </p>
        {#if view.presets.length}
          <ul class="presets">
            {#each view.presets as name (name)}
              <li>
                <span>{name}</span>
                <button onclick={() => conn.send('load_preset', { name })}>
                  Load
                </button>
                <button
                  class="danger"
                  onclick={() => conn.send('delete_preset', { name })}
                  aria-label="Delete preset {name}"
                >
                  &times;
                </button>
              </li>
            {/each}
          </ul>
        {/if}
        <div class="save-preset">
          <input
            bind:value={presetName}
            maxlength="30"
            placeholder="e.g. Friday game"
            aria-label="New preset name"
          />
          <button
            disabled={!presetName.trim()}
            onclick={() => {
              conn.send('save_preset', { name: presetName.trim() });
              presetName = '';
            }}
          >
            Save
          </button>
        </div>
      </section>

      <section>
        <h3>Players</h3>
        {#each view.seats as seat (seat.seat)}
          <div class="player">
            <span class="who">{seat.player.nick}</span>
            <span class="numeric">{seat.stack}</span>
            {#if editing === seat.player.id}
              <input
                class="numeric"
                type="number"
                min="0"
                bind:value={editAmount}
                aria-label="new stack"
              />
              <input
                placeholder="reason"
                bind:value={editReason}
                aria-label="reason"
              />
              <button onclick={saveStack}>Save</button>
              <button class="deny" onclick={() => (editing = null)}>Cancel</button>
            {:else}
              <button
                onclick={() => {
                  editing = seat.player.id;
                  editAmount = seat.stack;
                }}
              >
                Edit stack
              </button>
              <button
                onclick={() =>
                  conn.send('set_away', {
                    player_id: seat.player.id,
                    away: seat.state !== 'away',
                  })}
              >
                {seat.state === 'away' ? 'Bring back' : 'Set away'}
              </button>
              <button
                class="deny"
                onclick={() => conn.send('remove_player', { player_id: seat.player.id })}
              >
                Remove
              </button>
              <button
                onclick={() =>
                  conn.send('co_host', {
                    player_id: seat.player.id,
                    on: !seat.player.is_co_host,
                  })}
              >
                {seat.player.is_co_host ? 'Not co-host' : 'Make co-host'}
              </button>
            {/if}
          </div>
        {/each}
      </section>

      <section>
        <h3>The game</h3>
        <button onclick={() => conn.send('pause', { paused: !view.paused })}>
          {view.paused ? 'Resume dealing' : 'Pause after this hand'}
        </button>
        {#if confirmEnd}
          <p class="note">
            Ending the game cashes out every stack so the ledger is final.
            It cannot be undone.
          </p>
          <button class="deny" onclick={() => conn.send('end_game')}>
            Yes, end the game
          </button>
          <button onclick={() => (confirmEnd = false)}>Keep playing</button>
        {:else}
          <button class="deny" onclick={() => (confirmEnd = true)}>End the game</button>
        {/if}
      </section>
    </div>
  </section>
{/if}

<style>
  .presets {
    margin: 0 0 10px;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .presets li {
    display: grid;
    grid-template-columns: 1fr auto auto;
    align-items: center;
    gap: 6px;
    font-size: 13px;
  }

  .presets button {
    min-height: 36px;
    padding: 0 12px;
    font-size: 13px;
  }

  .presets .danger {
    color: var(--red);
    border-color: var(--red);
    padding: 0 10px;
  }

  .save-preset {
    display: flex;
    gap: 6px;
  }

  .save-preset button {
    min-height: 40px;
    padding: 0 14px;
    white-space: nowrap;
  }

  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(5, 4, 8, 0.6);
    border: 0;
    z-index: 22;
  }

  .panel {
    position: fixed;
    z-index: 23;
    right: 16px;
    top: 16px;
    bottom: 16px;
    width: min(420px, calc(100vw - 32px));
    display: flex;
    flex-direction: column;
    background: var(--bg-raised);
    border: 1px solid var(--plate-edge);
    border-radius: var(--radius);
    overflow: hidden;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 10px 10px 14px;
    border-bottom: 1px solid var(--plate-edge);
  }

  h2 {
    margin: 0;
    font-size: 15px;
  }

  h3 {
    margin: 0 0 6px;
    font-size: 12px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-dim);
  }

  .body {
    overflow: auto;
    padding: 12px 14px 20px;
    display: flex;
    flex-direction: column;
    gap: 18px;
    -webkit-overflow-scrolling: touch;
  }

  .note {
    margin: 0 0 8px;
    font-size: 12px;
    color: var(--text-dim);
    line-height: 1.5;
  }

  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    min-height: var(--tap);
    font-size: 13px;
  }

  .row input[type='number'],
  .row select {
    width: 140px;
    min-height: 38px;
    font: inherit;
    color: inherit;
    background: var(--bg);
    border: 1px solid var(--plate-edge);
    border-radius: 8px;
    padding: 0 8px;
    text-align: right;
  }

  .row.check {
    justify-content: flex-start;
  }

  .row input[type='checkbox'] {
    width: 20px;
    height: 20px;
    accent-color: var(--accent);
  }

  .request,
  .player {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    padding: 6px 0;
    border-top: 1px solid rgba(255, 255, 255, 0.05);
    font-size: 13px;
  }

  .player .who {
    min-width: 72px;
  }

  .request button,
  .player button {
    min-height: 34px;
    padding: 0 10px;
    font-size: 12px;
  }

  .player input {
    width: 90px;
    min-height: 34px;
    font-size: 12px;
  }

  .deny {
    color: var(--red);
    border-color: color-mix(in srgb, var(--red) 45%, transparent);
  }

  .primary {
    background: var(--accent);
    border-color: transparent;
    color: #1a1020;
    font-weight: 650;
    margin-top: 8px;
  }

  .numeric {
    font-family: var(--numerals);
  }

  @media (max-width: 860px) {
    .panel {
      left: 0;
      right: 0;
      top: auto;
      bottom: 0;
      width: 100%;
      height: 85vh;
      border-radius: var(--radius) var(--radius) 0 0;
      padding-bottom: env(safe-area-inset-bottom);
    }
  }
</style>
