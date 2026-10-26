<script>
  /**
   * Rabbit hunting (section 6.7): the cards the hand never reached, face
   * down in the board positions they would have filled.
   *
   * One strip across the lot rather than a card at a time — the question
   * is always "what would have come", never "what would the turn have
   * been, specifically". Turning them over shows them to everybody.
   */
  import Card from './Card.svelte';

  let { rabbit, fourColour = false, onRevealAll } = $props();

  let hidden = $derived(rabbit.cards.filter((c) => c === null).length);
</script>

<div class="rabbit">
  {#each rabbit.cards as card, index (index)}
    {#if card}
      <div class="revealed" title="rabbit hunted">
        <Card {card} size="board" {fourColour} />
      </div>
    {:else}
      <div class="face-down">
        <Card card={null} size="board" />
      </div>
    {/if}
  {/each}

  {#if hidden > 0}
    <button class="strip" onclick={onRevealAll}>Show cards</button>
  {/if}
</div>

<style>
  /* The undealt cards are one group inside the board row, so the strip
     has something to span. `gap: inherit` keeps them spaced exactly like
     the cards that were really dealt. */
  .rabbit {
    position: relative;
    display: flex;
    gap: inherit;
  }

  /* One translucent bar across the lot. Black on white so it reads over
     a card back at any felt colour. */
  .strip {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 0;
    padding: 0;
    border: none;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.78);
    color: #16141a;
    font-size: clamp(10px, 1.3cqw, 14px);
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    backdrop-filter: blur(1px);
    transition: background 120ms ease;
  }

  .strip:hover,
  .strip:focus-visible {
    background: rgba(255, 255, 255, 0.92);
  }

  .face-down {
    line-height: 0;
    opacity: 0.55;
  }

  /* A card that was never really dealt: shown, but visibly set apart from
     the board proper so nobody misreads the hand that was played. */
  .revealed {
    line-height: 0;
    opacity: 0.85;
    filter: saturate(0.75);
  }
</style>
