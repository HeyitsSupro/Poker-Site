<script>
  /**
   * One playing card, face up or face down. Original artwork: a big rank in
   * the corner and a large suit glyph, on a card back of our own.
   */
  import { parseCard, suitColour } from '../lib/cards.js';

  let { card = null, size = 'board', fourColour = false, dim = false } = $props();

  let face = $derived(card ? parseCard(card) : null);
  let colour = $derived(face ? suitColour(face.suit, fourColour) : 'black');
</script>

{#if face}
  <div
    class="card {size}"
    class:dim
    data-colour={colour}
    role="img"
    aria-label={face.name}
  >
    <span class="rank">{face.label}</span>
    <span class="suit">{face.glyph}</span>
  </div>
{:else}
  <div class="card back {size}" class:dim aria-label="face down card" role="img">
    <span class="weave"></span>
  </div>
{/if}

<style>
  .card {
    --w: 64px;
    position: relative;
    width: var(--w);
    aspect-ratio: 5 / 7;
    border-radius: calc(var(--w) * 0.11);
    background: #f7f5fa;
    color: #16141a;
    display: grid;
    place-items: center;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.45);
    user-select: none;
    flex: none;
  }

  .card.dim {
    opacity: 0.55;
  }

  .card[data-colour='red'] {
    color: #d6273f;
  }
  .card[data-colour='blue'] {
    color: #2563c9;
  }
  .card[data-colour='green'] {
    color: #1d8a52;
  }

  /* The rank is the thing you read across the table, so it is large and
     sits in the top-left corner. The suit is bigger too, and pushed down
     and left out of the rank's way rather than sitting under it. */
  .rank {
    position: absolute;
    top: 1%;
    left: 8%;
    font-size: calc(var(--w) * 0.46);
    font-weight: 700;
    line-height: 1;
    letter-spacing: -0.04em;
  }

  .suit {
    position: absolute;
    left: 6%;
    bottom: 2%;
    font-size: calc(var(--w) * 0.66);
    line-height: 1;
  }

  /* Sizes: the board is the showpiece, hole cards sit on a seat plate. */
  .board {
    --w: clamp(52px, 8vw, 96px);
  }
  /* Other players' cards are a hint that there is a hand there, not the
     focus: small enough to sit on the plate without crowding its neighbour. */
  .hole {
    --w: clamp(30px, 3.4vw, 44px);
  }
  .mine {
    --w: clamp(42px, 4.6vw, 60px);
  }
  .tiny {
    --w: 26px;
  }

  .back {
    background: linear-gradient(150deg, #3a2a52, #1f1430 60%);
    border: 1px solid rgba(255, 255, 255, 0.08);
  }

  .back .weave {
    position: absolute;
    inset: 9%;
    border-radius: calc(var(--w) * 0.07);
    background-image:
      repeating-linear-gradient(
        45deg,
        rgba(180, 124, 255, 0.28) 0 2px,
        transparent 2px 6px
      ),
      repeating-linear-gradient(
        -45deg,
        rgba(180, 124, 255, 0.18) 0 2px,
        transparent 2px 6px
      );
  }

  /* Card backs, chosen in Options and remembered on the device. Each is
     drawn in CSS from the accent colour, so they follow the theme and add
     nothing to the bundle. */
  :global(:root[data-card-back='lattice']) .back .weave {
    background-image:
      repeating-linear-gradient(
        0deg,
        rgba(180, 124, 255, 0.3) 0 1px,
        transparent 1px 7px
      ),
      repeating-linear-gradient(
        90deg,
        rgba(180, 124, 255, 0.3) 0 1px,
        transparent 1px 7px
      );
  }

  :global(:root[data-card-back='plain']) .back .weave {
    background-image: none;
    border: 1px solid rgba(180, 124, 255, 0.35);
  }
</style>
