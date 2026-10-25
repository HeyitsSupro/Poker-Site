<script>
  /**
   * Rabbit hunting (section 6.7): the cards the hand never reached, face
   * down in the board positions they would have filled. Anyone at the
   * table can turn one over, and it turns over for everybody. No vote, no
   * cost.
   *
   * This renders inside the board row, so the cards line up with the ones
   * that were actually dealt.
   */
  import Card from './Card.svelte';

  let { rabbit, fourColour = false, onReveal } = $props();
</script>

{#each rabbit.cards as card, index (index)}
  {#if card}
    <div class="revealed" title="rabbit hunted">
      <Card {card} size="board" {fourColour} />
    </div>
  {:else}
    <button
      class="hidden-card"
      onclick={() => onReveal(index)}
      aria-label="Turn over the card that would have come"
      title="See what would have come"
    >
      <Card card={null} size="board" />
    </button>
  {/if}
{/each}

<style>
  .hidden-card {
    display: block;
    padding: 0;
    border: none;
    background: none;
    /* The card sets the size and is already far larger than a 44px
       target, so the global minimum button height is not wanted here. */
    min-height: 0;
    line-height: 0;
    border-radius: 8px;
    opacity: 0.6;
    transition: opacity 140ms ease, transform 140ms ease;
  }

  .hidden-card:hover,
  .hidden-card:focus-visible {
    opacity: 1;
    transform: translateY(-4px);
  }

  /* A card that was never really dealt: shown, but visibly set apart from
     the board proper so nobody misreads the hand that was played. */
  .revealed {
    line-height: 0;
    opacity: 0.85;
    filter: saturate(0.75);
  }
</style>
