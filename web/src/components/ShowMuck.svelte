<script>
  /**
   * Show or muck at showdown (section 6.8).
   *
   * Only ever offered to a player who does not have to show: a hand that
   * wins or ties is turned over by the server. Showing one card instead of
   * both is a normal thing to want, so both are here.
   */
  let { cards = [], countdown = null, onShow, onMuck } = $props();
</script>

<div class="show-muck" role="group" aria-label="Show or muck">
  <div class="head">
    <strong>Show your hand?</strong>
    {#if countdown != null}
      <span class="count numeric">{Math.ceil(countdown)}s</span>
    {/if}
  </div>

  <div class="buttons">
    <button class="show" onclick={() => onShow([0, 1])}>Show both</button>
    {#if cards.length === 2}
      <button onclick={() => onShow([0])}>Show {cards[0]}</button>
      <button onclick={() => onShow([1])}>Show {cards[1]}</button>
    {/if}
    <button class="muck" onclick={onMuck}>Muck</button>
  </div>
  <p class="note">Mucking costs you nothing — you cannot win this pot.</p>
</div>

<style>
  .show-muck {
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

  .buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .buttons button {
    flex: 1 1 auto;
    padding: 0 12px;
    font-size: 13px;
  }

  .show {
    border-color: var(--green);
    color: var(--green);
  }

  .muck {
    border-color: var(--red);
    color: var(--red);
  }

  .note {
    margin: 0;
    font-size: 12px;
    color: var(--text-dim);
  }
</style>
