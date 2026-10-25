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

  let sizing = $state(false);
  let amount = $state(0);
  let input = $state(null);

  let canCheck = $derived(legal.actions?.includes('check'));
  let canCall = $derived(legal.actions?.includes('call'));
  let canRaise = $derived(
    legal.actions?.includes('raise') || legal.actions?.includes('bet'),
  );
  let raiseWord = $derived(
    !myTurn ? 'BET / RAISE' : legal.actions?.includes('bet') ? 'BET' : 'RAISE',
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
    {#if !myTurn}
      <div class="pre">
        <button
          class="wide"
          class:armed={preAction?.kind === 'check_fold'}
          onclick={() => onPreAction('check_fold')}
        >
          CHECK OR FOLD<kbd>I</kbd>
        </button>
        {#if preAction?.facing > 0}
          <button
            class="wide"
            class:armed={preAction?.kind === 'call'}
            onclick={() => onPreAction('call')}
          >
            CALL {money(preAction.facing)}
          </button>
        {/if}
      </div>
    {/if}

    <div class="row">
      <button
        class="act raise"
        disabled={!myTurn || !canRaise || busy}
        onclick={openSizing}
      >
        {raiseWord}<kbd>R</kbd>
      </button>
      <button
        class="act call"
        disabled={!myTurn || (!canCheck && !canCall) || busy}
        onclick={() => onAct(canCheck ? 'check' : 'call')}
      >
        {#if !myTurn}CHECK / CALL{:else if canCheck}CHECK{:else}CALL {money(toCall)}{/if}<kbd>K</kbd>
      </button>
      <button
        class="act fold"
        disabled={!myTurn || busy}
        onclick={() => onAct('fold')}
      >
        FOLD<kbd>F</kbd>
      </button>
    </div>
  {/if}
</div>

<style>
  .actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: min(440px, 100%);
    margin-left: auto;
  }

  .pre {
    display: flex;
    gap: 8px;
  }

  .wide {
    flex: 1;
    min-height: 40px;
    font-size: 12px;
    letter-spacing: 0.08em;
    background: transparent;
    color: var(--text-dim);
    position: relative;
  }

  .wide.armed {
    border-color: var(--accent);
    color: var(--accent);
    background: rgba(180, 124, 255, 0.1);
  }

  .row {
    display: flex;
    gap: 8px;
  }

  .act {
    flex: 1;
    min-height: 58px;
    background: transparent;
    font-size: 14px;
    font-weight: 650;
    letter-spacing: 0.06em;
    position: relative;
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
