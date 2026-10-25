<script>
  /**
   * Past hands: pick one from the list, then step through it.
   *
   * The server decides what a replay may contain — your own cards, the
   * hands turned face up at showdown, the board and the betting, and
   * nothing else. A hand somebody mucked stays mucked, so this component
   * draws what it is given and never asks for more.
   */
  import Card from './Card.svelte';

  let { conn, hands = [], replay = null, loading = false, fourColour = false } =
    $props();

  let step = $state(0);

  // Restart the walk-through whenever a different hand is opened.
  let openHand = $derived(replay?.hand_no ?? null);
  $effect(() => {
    openHand;
    step = 0;
  });

  let steps = $derived(replay?.steps ?? []);

  // How much of the board had been dealt by this point in the hand.
  let boardAt = $derived.by(() => {
    if (!replay) return [];
    const full = replay.boards?.[0] ?? [];
    let dealt = 0;
    for (const entry of steps.slice(0, step)) {
      if (entry.kind === 'board') dealt += entry.cards?.length ?? 0;
    }
    return full.slice(0, dealt);
  });

  function phrase(entry) {
    const who = entry.nick ?? `Seat ${entry.seat}`;
    switch (entry.kind) {
      case 'blind':
        return `${who} posts the ${entry.which} blind, ${entry.amount}`;
      case 'ante':
        return `${who} antes ${entry.amount}`;
      case 'straddle':
        return `${who} straddles ${entry.amount}`;
      case 'fold':
        return `${who} folds`;
      case 'check':
        return `${who} checks`;
      case 'call':
        return `${who} calls ${entry.amount}${entry.all_in ? ' and is all in' : ''}`;
      case 'raise':
        return `${who} raises to ${entry.to}${entry.all_in ? ' and is all in' : ''}`;
      case 'short_all_in':
        return `${who} is all in for ${entry.to}`;
      case 'board':
        return `${entry.street}: ${(entry.cards ?? []).join(' ')}`;
      case 'run_choice':
        return `${who} asks to run it ${entry.count}×`;
      case 'runs_settled':
        return `running it ${entry.runs}×`;
      case 'run_choice_open':
        return 'offering to run it more than once';
      case 'hand_complete':
        return 'hand complete';
      default:
        return entry.kind;
    }
  }
</script>

{#if replay}
  <div class="replay">
    <header class="bar">
      <button onclick={() => conn.send_history({})}>&larr; All hands</button>
      <strong>Hand #{replay.hand_no}</strong>
      <span class="numeric">pot {replay.result.pot}</span>
    </header>

    <div class="board-row">
      {#each boardAt as card (card)}
        <Card {card} size="mine" {fourColour} />
      {/each}
      {#each Array(Math.max(0, 5 - boardAt.length)) as _, slot (slot)}
        <div class="slot"></div>
      {/each}
    </div>

    <ul class="players">
      {#each replay.seats as seat (seat.seat)}
        <li class:you={seat.you}>
          <span class="nick">{seat.nick}</span>
          <span class="cards">
            {#if seat.cards}
              {#each seat.cards as card (card)}
                <Card {card} size="tiny" {fourColour} />
              {/each}
            {:else if seat.folded}
              <em>folded</em>
            {:else if replay.result.uncontested}
              <!-- Everyone else folded, so they never had to show. -->
              <em>not shown</em>
            {:else}
              <em>mucked</em>
            {/if}
          </span>
          <span class="won numeric" class:up={seat.won > 0}>
            {seat.won > 0 ? `+${seat.won}` : ''}
          </span>
        </li>
      {/each}
    </ul>

    <ol class="steps">
      {#each steps as entry, index (index)}
        <li class:done={index < step} class:now={index === step - 1}>
          {phrase(entry)}
        </li>
      {/each}
    </ol>

    <footer class="controls">
      <button onclick={() => (step = 0)} disabled={step === 0}>Start</button>
      <button onclick={() => (step = Math.max(0, step - 1))} disabled={step === 0}>
        Back
      </button>
      <button
        onclick={() => (step = Math.min(steps.length, step + 1))}
        disabled={step >= steps.length}
      >
        Step
      </button>
      <button onclick={() => (step = steps.length)} disabled={step >= steps.length}>
        End
      </button>
    </footer>
  </div>
{:else}
  <div class="list">
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if hands.length === 0}
      <p class="muted">No hands have been played yet.</p>
    {:else}
      <ul>
        {#each hands as item (item.hand_no)}
          <li>
            <button onclick={() => conn.send_history({ hand_no: item.hand_no })}>
              Hand #{item.hand_no}
              <time>{new Date(item.ended_at).toLocaleTimeString()}</time>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
{/if}

<style>
  .list ul,
  .players,
  .steps {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .list li button {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: none;
    border: none;
    border-bottom: 1px solid var(--plate-edge);
    border-radius: 0;
    text-align: left;
  }

  .list li button:hover {
    background: rgba(255, 255, 255, 0.05);
  }

  time,
  .muted {
    color: var(--text-dim);
    font-size: 12px;
  }

  .bar {
    display: flex;
    align-items: center;
    gap: 12px;
    justify-content: space-between;
    padding-bottom: 10px;
  }

  .bar button {
    min-height: 36px;
    font-size: 13px;
    padding: 0 10px;
  }

  .board-row,
  .cards {
    display: flex;
    gap: 4px;
  }

  .board-row {
    justify-content: center;
    padding: 10px 0;
    min-height: 70px;
  }

  .slot {
    width: 38px;
    border-radius: 6px;
    border: 1px dashed var(--plate-edge);
  }

  .players li {
    display: grid;
    grid-template-columns: 1fr auto 56px;
    align-items: center;
    gap: 10px;
    padding: 5px 0;
    border-bottom: 1px solid var(--plate-edge);
    font-size: 13px;
  }

  .players li.you .nick {
    color: var(--accent);
  }

  .players em {
    color: var(--text-dim);
    font-size: 12px;
  }

  .won {
    text-align: right;
    font-family: var(--numerals);
  }

  .won.up {
    color: var(--green);
  }

  .steps {
    margin-top: 10px;
    font-size: 13px;
    max-height: 180px;
    overflow-y: auto;
  }

  .steps li {
    padding: 3px 6px;
    color: var(--text-dim);
    border-radius: 6px;
  }

  .steps li.done {
    color: var(--text);
  }

  .steps li.now {
    background: color-mix(in srgb, var(--accent) 22%, transparent);
    color: var(--text);
  }

  .controls {
    display: flex;
    gap: 6px;
    padding-top: 10px;
  }

  .controls button {
    flex: 1;
    min-height: 40px;
    font-size: 13px;
    padding: 0;
  }
</style>
