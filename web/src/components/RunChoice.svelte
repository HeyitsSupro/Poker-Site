<script>
  /**
   * How many times to run the board (section 6.6).
   *
   * Everyone still in the hand picks a number and confirms. The lowest
   * number anybody asks for is the one used, so this says so plainly
   * rather than letting someone think their three is a promise.
   */
  let { runChoice, countdown = null, mySeat = null, onChoose } = $props();

  let picked = $state(1);

  let options = $derived(
    Array.from({ length: Math.max(1, runChoice.max_runs) }, (_, i) => i + 1),
  );
  let mine = $derived(mySeat != null && runChoice.voters.includes(mySeat));
  let confirmed = $derived(
    mySeat != null && runChoice.chosen[String(mySeat)] != null,
  );
  let waitingOn = $derived(
    runChoice.voters.filter((seat) => runChoice.chosen[String(seat)] == null).length,
  );
</script>

<div class="run-choice" role="group" aria-label="Run it more">
  <div class="head">
    <strong>Run it how many times?</strong>
    {#if countdown != null}
      <span class="count numeric">{Math.ceil(countdown)}s</span>
    {/if}
  </div>

  {#if mine && !confirmed}
    <div class="picker">
      {#each options as option (option)}
        <button
          class:selected={picked === option}
          onclick={() => (picked = option)}
          aria-pressed={picked === option}
        >
          {option}
        </button>
      {/each}
    </div>
    <button class="confirm" onclick={() => onChoose(picked)}>
      Confirm {picked}
    </button>
    <p class="note">The lowest number anyone picks is the one used.</p>
  {:else if mine}
    <p class="note">
      You asked for {runChoice.chosen[String(mySeat)]}.
      {#if waitingOn > 0}Waiting for {waitingOn} more.{/if}
    </p>
  {:else}
    <p class="note">The players still in the hand are choosing.</p>
  {/if}
</div>

<style>
  .run-choice {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px 14px;
    border-radius: var(--radius);
    background: rgba(12, 10, 16, 0.9);
    border: 1px solid var(--plate-edge);
    min-width: 230px;
  }

  .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
  }

  .count {
    font-family: var(--numerals);
    color: var(--amber);
  }

  .picker {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .picker button {
    min-width: 44px;
    min-height: 44px;
    padding: 0;
    font-variant-numeric: tabular-nums;
  }

  .picker button.selected {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 24%, transparent);
  }

  .confirm {
    background: var(--accent);
    border-color: transparent;
    color: #1a1020;
    font-weight: 650;
  }

  .note {
    margin: 0;
    font-size: 12px;
    color: var(--text-dim);
    line-height: 1.4;
  }
</style>
