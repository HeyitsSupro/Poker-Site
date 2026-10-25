<script>
  /**
   * One seat around the felt: the plate with a name and a stack, the two
   * cards overlapping it, the badges, and the chips bet in front.
   */
  import Card from './Card.svelte';
  import NoteButton from './NoteButton.svelte';

  let {
    seat = null,
    number,
    isMe = false,
    isDealer = false,
    isTurn = false,
    fourColour = false,
    bigBlind = 2,
    inBigBlinds = false,
    onSit,
    // Seconds left on this player's clock, and how long it started with,
    // so the bar along the plate can be drawn without a timer of its own.
    countdown = null,
    countdownOf = null,
    onBank = false,
    // {win, tie} while hands are face up and the board is still to come.
    equity = null,
    reaction = null,
    // Your own note about this player, and the saver for it.
    note = '',
    onNote = null,
  } = $props();

  let player = $derived(seat?.player);
  let cards = $derived(seat?.cards ?? null);
  let backs = $derived(!cards && (seat?.card_count ?? 0) > 0 ? seat.card_count : 0);

  function money(value) {
    if (value == null) return '';
    if (!inBigBlinds) return value.toLocaleString();
    return `${(value / bigBlind).toFixed(1)} BB`;
  }

  let label = $derived.by(() => {
    if (!seat) return null;
    if (seat.state === 'away') return 'AWAY';
    if (seat.state === 'waiting') return 'IN NEXT HAND';
    if (seat.folded) return 'FOLD';
    if (seat.all_in) return 'ALL IN';
    if (seat.leaving) return 'LEAVING';
    return null;
  });

  // The bar shrinks from full to empty. Width is set from the server's
  // numbers rather than animated locally, so a reconnect lands it in the
  // right place instead of starting over.
  let barPercent = $derived(
    countdown == null || !countdownOf ? 0 : Math.max(0, Math.min(100, (countdown / countdownOf) * 100)),
  );
  let urgent = $derived(countdown != null && countdown <= 5);
</script>

<div class="seat" class:empty={!seat} class:turn={isTurn} class:folded={seat?.folded}>
  {#if seat}
    {#if player.hands_won > 0 || player.rebuys > 0}
      <div class="badges">
        {#if player.hands_won > 0}
          <span class="badge won" title="hands won">&#9733; {player.hands_won}</span>
        {/if}
        {#if player.rebuys > 0}
          <span class="badge rebuy" title="rebuys">&#8635; {player.rebuys}</span>
        {/if}
      </div>
    {/if}

    <div class="plate">
      {#if cards || backs}
        <!-- Cards sit on the shoulder of the plate: your own large enough
             to read at a glance, everyone else's just enough to show there
             is a hand there. Never over the name. -->
        <div class="cards" class:mine={isMe}>
          {#if cards}
            {#each cards as card (card)}
              <Card
                {card}
                size={isMe ? 'mine' : 'hole'}
                {fourColour}
                dim={seat.folded}
              />
            {/each}
          {:else}
            {#each Array(backs) as _, index (index)}
              <Card card={null} size="hole" dim={seat.folded} />
            {/each}
          {/if}
        </div>
      {/if}
      <div class="who">
        <span class="nick">{player.nick}</span>
        {#if onNote && !isMe}
          <NoteButton about={player.id} nick={player.nick} text={note} onSave={onNote} />
        {/if}
        <span class="stack numeric">{money(seat.stack)}</span>
        {#if label}
          <span class="label">{label}</span>
        {/if}
      </div>
    </div>

    {#if countdown != null}
      <div
        class="clock"
        class:bank={onBank}
        class:urgent
        role="timer"
        aria-label="{Math.ceil(countdown)} seconds to act"
      >
        <i style="width: {barPercent}%"></i>
      </div>
    {/if}

    {#if equity}
      <div class="equity numeric" title="chance of winning this hand">
        {equity.win.toFixed(0)}%{#if equity.tie >= 0.5}<small>
            &nbsp;tie {equity.tie.toFixed(0)}%</small
          >{/if}
      </div>
    {/if}

    {#if reaction}
      <div class="reaction" aria-hidden="true">{reaction}</div>
    {/if}

    {#if isMe && seat.hand_name}
      <div class="hand-name">{seat.hand_name}</div>
    {/if}

    {#if seat.committed > 0}
      <div class="bet numeric"><i class="chip"></i>{money(seat.committed)}</div>
    {/if}
  {:else}
    <button class="sit" onclick={() => onSit(number)}>Sit</button>
  {/if}

  {#if isDealer}
    <span class="dealer" title="dealer">D</span>
  {/if}
</div>

<style>
  .seat {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    width: max-content;
  }

  .cards {
    display: flex;
    /* Raised so the cards break the top edge of the plate, the way they do
       on a real table, without covering anything. */
    margin-top: -26px;
    margin-bottom: -4px;
    z-index: 1;
    flex: none;
  }

  .cards.mine {
    margin-top: -36px;
    margin-bottom: -8px;
  }

  .cards > :global(*:nth-child(2)) {
    margin-left: -16%;
    transform: rotate(5deg);
  }

  .plate {
    position: relative;
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 108px;
    padding: 6px 14px;
    border-radius: var(--radius);
    /* Token-driven so the light theme follows without a second rule. */
    background: var(--plate);
    border: 1px solid var(--plate-edge);
    line-height: 1.25;
  }

  .who {
    display: flex;
    flex-direction: column;
    align-items: center;
    flex: 1;
    /* Wide enough that the name and stack always have their own room,
       whatever size the cards beside them are. */
    min-width: 68px;
  }

  /* Whose turn it is: the plate lights up, as in the reference
     screenshot. The pair inverts with the theme. */
  .turn .plate {
    background: var(--text);
    border-color: var(--text);
    color: var(--bg);
  }

  .folded .plate {
    opacity: 0.5;
  }

  .nick {
    font-size: 13px;
    max-width: 12ch;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .stack {
    font-family: var(--numerals);
    font-size: 17px;
    font-variant-numeric: tabular-nums;
  }

  .label {
    font-size: 10px;
    letter-spacing: 0.12em;
    color: var(--text-dim);
  }

  .turn .label {
    color: var(--bg-raised);
    opacity: 0.75;
  }

  /* The countdown runs along the bottom edge of the plate, as in the
     reference screenshot. */
  .clock {
    width: 100%;
    height: 3px;
    margin-top: -1px;
    border-radius: 0 0 var(--radius) var(--radius);
    background: rgba(255, 255, 255, 0.12);
    overflow: hidden;
  }

  .clock i {
    display: block;
    height: 100%;
    background: var(--accent);
    /* Eased rather than stepped, so a one-second snapshot reads smoothly. */
    transition: width 1s linear;
  }

  .clock.bank i {
    background: var(--amber);
  }

  .clock.urgent i {
    background: var(--red);
  }

  .equity {
    margin-top: 3px;
    padding: 1px 8px;
    border-radius: 999px;
    background: rgba(12, 10, 16, 0.72);
    color: var(--text-dim);
    font-family: var(--numerals);
    font-size: 11px;
    white-space: nowrap;
  }

  .equity small {
    opacity: 0.7;
  }

  .reaction {
    position: absolute;
    top: -10px;
    right: -14px;
    font-size: 22px;
    pointer-events: none;
    animation: pop 240ms ease-out;
  }

  @keyframes pop {
    from {
      transform: scale(0.4) translateY(6px);
      opacity: 0;
    }
    to {
      transform: scale(1) translateY(0);
      opacity: 1;
    }
  }

  .hand-name {
    margin-top: 2px;
    padding: 2px 10px;
    border-radius: 999px;
    background: var(--red);
    color: #2a0a0f;
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.04em;
    white-space: nowrap;
  }

  .badges {
    display: flex;
    gap: 4px;
    align-self: flex-end;
    margin-bottom: -6px;
    z-index: 2;
  }

  .badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 999px;
    font-weight: 650;
  }

  .won {
    background: #1f7a4d;
    color: #d8ffe9;
  }

  .rebuy {
    background: #8e2230;
    color: #ffe0e4;
  }

  .bet {
    position: absolute;
    top: calc(100% + 6px);
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 2px 9px;
    border-radius: 999px;
    background: rgba(10, 8, 14, 0.72);
    font-family: var(--numerals);
    font-size: 12px;
    white-space: nowrap;
  }

  .chip {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--amber);
    box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.25) inset;
  }

  .dealer {
    position: absolute;
    right: -16px;
    bottom: 2px;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    background: #ded7e8;
    color: #221d2b;
    font-size: 11px;
    font-weight: 700;
  }

  .sit {
    min-height: 38px;
    padding: 0 20px;
    font-size: 13px;
    /* Reads as an empty chair in either theme: the plate colour, dimmed,
       with a dashed edge. */
    background: color-mix(in srgb, var(--plate) 70%, transparent);
    border-style: dashed;
    color: var(--text-dim);
  }

  .sit:hover {
    color: var(--text);
  }

  /* Phones: smaller plates, so a ring of ten still fits across 360px
     without the page scrolling sideways. */
  @media (max-width: 860px) {
    .plate {
      min-width: 84px;
      padding: 5px 10px;
      gap: 6px;
    }

    .who {
      min-width: 54px;
    }

    .nick {
      font-size: 12px;
      max-width: 9ch;
    }

    .stack {
      font-size: 15px;
    }
  }
</style>
