<script>
  /**
   * The action area, bottom right.
   *
   * A wide pre-action button sits above the three big ones. Pre-actions are
   * what you press while waiting for your turn:
   *
   *   CHECK OR FOLD — check if you can, fold if you cannot;
   *   CALL <amount> — locks in that exact amount, and clears itself if
   *                   anyone raises before your turn. There is deliberately
   *                   no "call any": nobody should ever accidentally call a
   *                   raise they never saw.
   *
   * The server decides what is legal and what a raise may be; this only
   * draws it. The range comes down with every state, so the slider cannot
   * offer an amount that would be refused.
   */
  import { tick } from 'svelte';

  let {
    legal = { actions: [] },
    myTurn = false,
    bigBlind = 2,
    potTotal = 0,
    inBigBlinds = false,
    busy = false,
    preAction = null,
    onAct,
    onPreAction,
  } = $props();

  // What it would cost to call right now, used by the pre-action row.
  let facing = $derived(preAction?.facing ?? 0);

  let sizing = $state(false);
  let amount = $state(0);
  let input = $state(null);

  let canCheck = $derived(legal.actions?.includes('check'));
  let canCall = $derived(legal.actions?.includes('call'));
  let canRaise = $derived(
    legal.actions?.includes('raise') || legal.actions?.includes('bet'),
  );
  // Never "BET / RAISE": only the one that is actually possible. Off
  // turn, a bet is possible when nobody has bet yet.
  let raiseWord = $derived(
    myTurn
      ? legal.actions?.includes('bet')
        ? 'BET'
        : 'RAISE'
      : facing > 0
        ? 'RAISE'
        : 'BET',
  );
  let low = $derived(legal.min_raise_to ?? 0);
  let high = $derived(legal.max_raise_to ?? 0);
  let toCall = $derived(legal.to_call ?? 0);

  // Close the sizing controls whenever it stops being our turn.
  $effect(() => {
    if (!myTurn) sizing = false;
  });

  function money(value) {
    return inBigBlinds ? `${(value / bigBlind).toFixed(1)} BB` : value.toLocaleString();
  }

  async function openSizing() {
    amount = low;
    sizing = true;
    await tick();
    input?.focus();
  }

  function quick(fraction) {
    // Pot-sized raises: call first, then bet a fraction of what is then in
    // the middle. Clamped, because a limit may cap it.
    const base = potTotal + toCall;
    const target = (legal.current_bet ?? 0) + Math.round(base * fraction);
    amount = Math.min(high, Math.max(low, target));
  }

  function confirmRaise() {
    const value = Math.min(high, Math.max(low, Math.round(amount)));
    sizing = false;
    onAct(legal.actions.includes('bet') ? 'bet' : 'raise', value);
  }
</script>

<div class="actions">
  {#if myTurn && sizing}
    <div class="sizing">
      <div class="quick">
        <button onclick={() => (amount = low)}>Min</button>
        <button onclick={() => quick(0.5)}>½ pot</button>
        <button onclick={() => quick(0.75)}>¾ pot</button>
        <button onclick={() => quick(1)}>Pot</button>
        <button onclick={() => (amount = high)}>All in</button>
      </div>
      <div class="slider">
        <input
          type="range"
          min={low}
          max={high}
          step="1"
          bind:value={amount}
          aria-label="raise to"
        />
        <input
          class="number numeric"
          type="number"
          min={low}
          max={high}
          step="1"
          bind:this={input}
          bind:value={amount}
          onkeydown={(e) => e.key === 'Enter' && confirmRaise()}
          aria-label="amount to raise to"
        />
      </div>
      {#if legal.capped_by}
        <p class="capped">Capped at {high} by the {legal.capped_by}.</p>
      {/if}
      <div class="confirm">
        <button class="ghost" onclick={() => (sizing = false)}>Back</button>
        <button class="go" onclick={confirmRaise} disabled={busy}>
          {raiseWord} to {money(amount)}
        </button>
      </div>
    </div>
  {:else}
    <!-- The pre-action row. It is always present, so arming something
         never changes the height of this column and never nudges the
         table. It shares the grid of the row below: CALL takes the first
         column, on its own at the left, and CHECK OR FOLD spans the other
         three, starting where CHECK starts so the two never overlap. -->
    <div class="pre" class:hidden={myTurn}>
      <div class="pre-slot">
        {#if facing > 0}
          <button
            class="pre-btn"
            class:armed={preAction?.kind === 'call'}
            aria-pressed={preAction?.kind === 'call'}
            onclick={() => onPreAction('call')}
          >
            CALL {money(facing)}
          </button>
        {/if}
      </div>
      <button
        class="pre-btn wide"
        class:armed={preAction?.kind === 'check_fold'}
        aria-pressed={preAction?.kind === 'check_fold'}
        onclick={() => onPreAction('check_fold')}
      >
        CHECK OR FOLD<kbd>I</kbd>
      </button>
    </div>

    <div class="row">
      <button
        class="act raise"
        disabled={!myTurn || !canRaise || busy}
        onclick={openSizing}
      >
        {raiseWord}<kbd>R</kbd>
      </button>

      <!-- CHECK is its own button and can be armed in advance. Once
           somebody bets there is nothing to check, so it is disabled and
           any arming of it has already been cleared. -->
      <button
        class="act check"
        class:armed={!myTurn && preAction?.kind === 'check'}
        aria-pressed={!myTurn && preAction?.kind === 'check'}
        disabled={myTurn ? !canCheck || busy : facing > 0}
        onclick={() => (myTurn ? onAct('check') : onPreAction('check'))}
      >
        CHECK<kbd>K</kbd>
      </button>

      <!-- CALL takes the same slot on your turn, where the price is known. -->
      <div class="call-slot">
        {#if myTurn && canCall}
          <button class="act call" disabled={busy} onclick={() => onAct('call')}>
            CALL {money(toCall)}<kbd>C</kbd>
          </button>
        {/if}
      </div>

      <button
        class="act fold"
        class:armed={!myTurn && preAction?.kind === 'fold'}
        aria-pressed={!myTurn && preAction?.kind === 'fold'}
        disabled={myTurn && busy}
        onclick={() => (myTurn ? onAct('fold') : onPreAction('fold'))}
      >
        FOLD<kbd>F</kbd>
      </button>
    </div>
  {/if}
</div>

<style>
  /* A fixed width, and rows that are always the same height whatever is
     showing. The action area shares a grid column with the felt, so if it
     changed size when your turn came round, the table would jump. */
  .actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: 460px;
    max-width: 100%;
    margin-left: auto;
  }

  .pre {
    display: grid;
    /* The same four columns as the row below, so the pre-actions line up
       with the buttons they stand in for. CALL sits alone in the first
       column; the left slot is held open even when there is nothing to
       call, so CHECK OR FOLD never slides underneath it. */
    grid-template-columns: 1.15fr 1fr 1fr 1fr;
    gap: 8px;
    height: 40px;
  }

  /* From the CHECK column to the right-hand edge. */
  .pre-btn.wide {
    grid-column: 2 / -1;
  }

  /* Hidden rather than removed, so the row below never moves up. */
  .pre.hidden {
    visibility: hidden;
  }

  .pre-slot {
    display: flex;
  }

  .pre-btn {
    flex: 1;
    min-height: 40px;
    font-size: 12px;
    letter-spacing: 0.08em;
    background: transparent;
    color: var(--text-dim);
    position: relative;
  }

  .pre-btn.armed,
  .act.armed {
    border-color: var(--accent);
    color: var(--accent);
    background: color-mix(in srgb, var(--accent) 16%, transparent);
  }

  .row {
    display: grid;
    /* Four fixed slots: bet/raise, check, call, fold. The call slot stays
       empty off turn rather than collapsing, so the other three never
       shuffle sideways underneath the pointer. */
    grid-template-columns: 1.15fr 1fr 1fr 1fr;
    gap: 8px;
    height: 58px;
  }

  .call-slot {
    display: flex;
  }

  .act {
    flex: 1;
    width: 100%;
    min-height: 58px;
    background: transparent;
    font-size: 14px;
    font-weight: 650;
    letter-spacing: 0.06em;
    position: relative;
  }

  .act.check {
    color: var(--green);
    border-color: color-mix(in srgb, var(--green) 55%, transparent);
  }

  .act.call {
    color: var(--green);
    border-color: color-mix(in srgb, var(--green) 55%, transparent);
  }

  .act.fold {
    color: var(--red);
    border-color: color-mix(in srgb, var(--red) 55%, transparent);
  }

  .act.raise {
    color: var(--text);
  }

  kbd {
    position: absolute;
    top: -8px;
    right: 10px;
    padding: 0 5px;
    font-family: var(--numerals);
    font-size: 10px;
    color: var(--text-dim);
    background: var(--bg);
    border-radius: 4px;
  }

  /* The shortcut hints are desktop-only: there is no keyboard on a phone. */
  @media (hover: none), (max-width: 720px) {
    kbd {
      display: none;
    }
  }

  .sizing {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 12px;
    border-radius: var(--radius);
    background: var(--bg-raised);
    border: 1px solid var(--plate-edge);
  }

  .quick {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .quick button {
    flex: 1;
    min-width: 62px;
    min-height: 38px;
    font-size: 12px;
    padding: 0 8px;
  }

  .slider {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .slider input[type='range'] {
    flex: 1;
    accent-color: var(--accent);
    min-height: var(--tap);
  }

  .number {
    width: 110px;
    font-family: var(--numerals);
    text-align: right;
  }

  .capped {
    margin: 0;
    font-size: 12px;
    color: var(--amber);
  }

  .confirm {
    display: flex;
    gap: 8px;
  }

  .ghost {
    background: transparent;
    color: var(--text-dim);
    flex: 0 0 90px;
  }

  .go {
    flex: 1;
    background: var(--accent);
    border-color: transparent;
    color: #1a1020;
    font-weight: 650;
  }

  @media (max-width: 720px) {
    .actions {
      width: 100%;
    }

    .act {
      min-height: 54px;
      font-size: 13px;
    }
  }
</style>
