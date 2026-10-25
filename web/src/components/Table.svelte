<script>
  /**
   * The table screen.
   *
   * Desktop follows the reference screenshot: a dark page, one purple felt
   * with the seats around it, the pot and board in the middle, a thin rail
   * on the left, log and chat bottom left, the action area bottom right.
   * The phone layout keeps every one of those elements and stacks them for
   * a portrait screen; both are in this one component so they cannot drift
   * apart.
   *
   * The client implements no poker rules. What a player may do arrives in
   * `view.you.legal` and is drawn; anything else is refused by the server
   * with a reason, which is shown.
   */
  import { SITE_NAME } from '../config.js';
  import { CARD_BACKS, FELTS } from '../lib/prefs.js';
  import ActionBar from './ActionBar.svelte';
  import Card from './Card.svelte';
  import Chat from './Chat.svelte';
  import HostPanel from './HostPanel.svelte';
  import Panel from './Panel.svelte';
  import Rabbit from './Rabbit.svelte';
  import Reactions from './Reactions.svelte';
  import RunChoice from './RunChoice.svelte';
  import Seat from './Seat.svelte';
  import ShowMuck from './ShowMuck.svelte';

  let {
    view,
    status,
    error = null,
    busy = false,
    prefs,
    conn,
    onPrefs,
    // Hand history: answers to a request, not part of the table snapshot.
    hands = [],
    replay = null,
    historyLoading = false,
  } = $props();

  let panelOpen = $state(false);
  let panelTab = $state('log');
  // On a phone the chat starts collapsed: the action area needs the room,
  // and the on-screen keyboard must not be able to cover it.
  let chatOpen = $state(
    typeof window === 'undefined' || !window.matchMedia('(max-width: 860px)').matches,
  );
  let optionsOpen = $state(false);
  let hostOpen = $state(false);
  let preAction = $state(null);
  let chat;

  let settings = $derived(view.settings);
  let me = $derived(view.you);
  let mySeat = $derived(me?.seat ?? null);
  let hand = $derived(view.hand);
  let legal = $derived(me?.legal ?? { actions: [] });
  let myTurn = $derived((legal.actions?.length ?? 0) > 0);
  let seatCount = $derived(settings.seat_count);
  let bySeat = $derived(
    Object.fromEntries(view.seats.map((seat) => [seat.seat, seat])),
  );
  let boards = $derived(hand?.boards?.length ? hand.boards : [hand?.board ?? []]);

  // --- countdowns -------------------------------------------------------
  //
  // The server sends how many seconds are left at the moment a snapshot is
  // built, which sidesteps every clock-skew problem. Between snapshots the
  // number is counted down here so the bar moves; each new snapshot
  // re-anchors it, so a reconnect lands on the truth rather than on
  // whatever the browser had drifted to.
  let tickNow = $state(Date.now());

  $effect(() => {
    if (!view.clock) return;
    const id = setInterval(() => (tickNow = Date.now()), 250);
    return () => clearInterval(id);
  });

  let anchoredAt = $state(Date.now());
  let anchoredLeft = $state(null);

  $effect(() => {
    // Re-anchor whenever the server's number changes.
    const left = view.clock?.seconds_left ?? null;
    anchoredLeft = left;
    anchoredAt = Date.now();
  });

  let secondsLeft = $derived(
    anchoredLeft == null
      ? null
      : Math.max(0, anchoredLeft - (tickNow - anchoredAt) / 1000),
  );

  // How long this countdown started with, for drawing the bar.
  let clockTotal = $derived.by(() => {
    const kind = view.clock?.kind;
    if (kind === 'action') {
      return view.clock.phase === 'bank'
        ? settings.time_bank_seconds
        : settings.action_seconds;
    }
    if (kind === 'run_choice') return settings.run_timer_seconds;
    if (kind === 'showdown') return settings.showdown_seconds;
    if (kind === 'next_hand') return settings.between_hands_seconds;
    return null;
  });

  let actionClockSeat = $derived(
    view.clock?.kind === 'action' ? view.clock.seat : null,
  );

  let reactionBySeat = $derived(
    Object.fromEntries((view.reactions ?? []).map((r) => [r.seat, r.emoji])),
  );

  let myCards = $derived(
    mySeat == null ? [] : (bySeat[mySeat]?.cards ?? []),
  );

  let myNotes = $derived(me?.notes ?? {});
  let onWaitlist = $derived((view.waitlist ?? []).some((w) => w.you));
  let tableFull = $derived(view.seats.length >= settings.seat_count);

  // --- your-turn alert --------------------------------------------------
  //
  // Sound, a buzz on a phone, and a flashing tab title, because people put
  // the table in the background between hands and a missed turn times out.
  let wasMyTurn = $state(false);

  $effect(() => {
    const mine = myTurn;
    if (mine && !wasMyTurn) alertMyTurn();
    wasMyTurn = mine;
  });

  function alertMyTurn() {
    if (prefs.sound) beep();
    try {
      navigator.vibrate?.(180);
    } catch {
      /* not supported, or blocked */
    }
    flashTitle();
  }

  function beep() {
    try {
      const Ctx = window.AudioContext ?? window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = 660;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.14, ctx.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.3);
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.32);
      setTimeout(() => ctx.close(), 600);
    } catch {
      /* autoplay policy, or no audio device */
    }
  }

  let titleTimer = null;

  function flashTitle() {
    if (document.visibilityState === 'visible') return;
    const original = document.title;
    let on = false;
    clearInterval(titleTimer);
    titleTimer = setInterval(() => {
      on = !on;
      document.title = on ? '● Your turn' : original;
    }, 900);
    const stop = () => {
      clearInterval(titleTimer);
      document.title = original;
      document.removeEventListener('visibilitychange', stop);
    };
    document.addEventListener('visibilitychange', stop);
  }

  /**
   * Where each chair sits around the felt, as a percentage of the table
   * area. Your own is always the one at the bottom, and the rest go round
   * clockwise from there.
   *
   * These are placed by hand rather than spread evenly round an ellipse,
   * for the reason the reference screenshot does the same: an even spread
   * bunches the side seats together, where the plates are tallest. A ten
   * seat table wants three along the bottom, two up each side and three
   * across the top.
   */
  const LAYOUTS = {
    2: [[50, 102], [50, -4]],
    3: [[50, 102], [6, 24], [94, 24]],
    4: [[50, 102], [1, 50], [50, -4], [99, 50]],
    5: [[50, 102], [4, 78], [16, 2], [84, 2], [96, 78]],
    6: [[50, 102], [10, 90], [1, 42], [50, -4], [99, 42], [90, 90]],
    7: [[50, 102], [13, 93], [0, 50], [22, 0], [78, 0], [100, 50], [87, 93]],
    8: [
      [50, 102], [15, 94], [0, 56], [16, 2],
      [50, -4], [84, 2], [100, 56], [85, 94],
    ],
    9: [
      [50, 102], [17, 95], [0, 64], [3, 26], [34, -2],
      [66, -2], [97, 26], [100, 64], [83, 95],
    ],
    10: [
      [50, 102], [20, 96], [1, 70], [1, 36], [19, 0],
      [50, -4], [81, 0], [99, 36], [99, 70], [80, 96],
    ],
  };

  // A phone is portrait, so the felt is taller than it is wide and the
  // chairs run down the sides instead of along the top and bottom. The
  // plates are a big fraction of a 360px screen, so the ring is kept well
  // inside the edges: a seat that hangs off the side would make the whole
  // page scroll sideways, which section 8.4 rules out.
  let portrait = $state(false);
  $effect(() => {
    const query = window.matchMedia('(max-width: 860px)');
    const update = () => (portrait = query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  });

  let places = $derived.by(() => {
    const anchor = mySeat ?? 0;
    const count = Math.max(2, Math.min(10, seatCount));
    const layout = LAYOUTS[count];

    return Array.from({ length: seatCount }, (_, number) => {
      const slot = (number - anchor + seatCount) % seatCount;
      if (!portrait) {
        const [x, y] = layout[slot % layout.length];
        return { number, x, y };
      }
      const angle = Math.PI / 2 + (slot * 2 * Math.PI) / seatCount;
      return {
        number,
        x: 50 + 36 * Math.cos(angle),
        y: 52 + 45 * Math.sin(angle),
      };
    });
  });

  let facing = $derived.by(() => {
    if (!hand || mySeat == null) return 0;
    const seat = bySeat[mySeat];
    if (!seat) return 0;
    return Math.max(0, (hand.current_bet ?? 0) - (seat.committed ?? 0));
  });

  // A locked-in pre-action clears itself the moment the price changes, so
  // nobody ever calls a raise they did not see.
  $effect(() => {
    if (preAction?.kind === 'call' && preAction.facing !== facing) preAction = null;
  });

  // When our turn arrives, play whatever was armed.
  $effect(() => {
    if (!myTurn || !preAction) return;
    const armed = preAction;
    preAction = null;
    if (armed.kind === 'check_fold') {
      act(legal.actions.includes('check') ? 'check' : 'fold');
    } else if (armed.kind === 'call' && armed.facing === (legal.to_call ?? 0)) {
      act('call');
    }
  });

  function act(action, amount = null) {
    conn.send('act', { action, amount });
  }

  function setPreAction(kind) {
    if (preAction?.kind === kind) {
      preAction = null;
    } else {
      preAction = { kind, facing };
    }
  }

  function sit(number) {
    const held = me?.held ?? 0;
    conn.send('sit', {
      seat: number,
      resume: held > 0,
      buy_in: held > 0 ? null : settings.buy_in,
    });
  }

  function openPanel(tab) {
    panelTab = tab;
    panelOpen = true;
  }

  function onKey(event) {
    const typing = ['INPUT', 'TEXTAREA'].includes(event.target.tagName);
    const key = event.key.toLowerCase();

    if (key === 'escape') {
      panelOpen = false;
      optionsOpen = false;
      return;
    }
    if (typing) return;

    if (key === 'l') {
      event.preventDefault();
      openPanel('log');
    } else if (key === 'm') {
      event.preventDefault();
      chatOpen = true;
      queueMicrotask(() => chat?.focusField());
    } else if (key === 'i') {
      event.preventDefault();
      if (!myTurn) setPreAction('check_fold');
    } else if (myTurn) {
      if (key === 'f') {
        event.preventDefault();
        act('fold');
      } else if (key === 'k') {
        event.preventDefault();
        act(legal.actions.includes('check') ? 'check' : 'call');
      }
    }
  }

  function money(value) {
    if (value == null) return '0';
    return prefs.inBigBlinds
      ? `${(value / settings.big_blind).toFixed(1)} BB`
      : value.toLocaleString();
  }

  let structureLabel = $derived(
    { 'no-limit': 'NLH', 'pot-limit': 'PLH', 'fixed-limit': 'FLH' }[
      settings.structure
    ] ?? 'NLH',
  );
</script>

<svelte:window onkeydown={onKey} />

<div class="screen">
  <!-- left rail -->
  <nav class="rail">
    <button onclick={() => (optionsOpen = !optionsOpen)}>
      <span class="glyph">&#9776;</span>Options
    </button>
    {#if me?.is_host}
      <button onclick={() => (hostOpen = true)}>
        <span class="glyph">&#9881;</span>Host
      </button>
    {/if}
    {#if mySeat != null}
      <button onclick={() => conn.send('stand')}>
        <span class="glyph">&#8617;</span>Leave seat
      </button>
      <button
        class:on={bySeat[mySeat]?.state === 'away'}
        onclick={() =>
          conn.send('away', { away: bySeat[mySeat]?.state !== 'away' })}
      >
        <span class="glyph">&#9679;</span>Away
      </button>
    {/if}
  </nav>

  <!-- the felt -->
  <main>
    <div class="table-area">
      <div class="felt">
        <div class="pot-row">
          <div class="pot numeric">{money(hand?.pot ?? 0)}</div>
        </div>

        <div class="boards">
          {#each boards as board, index (index)}
            {#if board.length || (index === 0 && view.rabbit)}
              <div class="board">
                {#each board as card (card)}
                  <Card {card} size="board" fourColour={prefs.fourColour} />
                {/each}
                {#if index === 0 && view.rabbit}
                  <!-- Rabbit hunting: the cards that never came, sitting in
                       the board positions they would have filled. -->
                  <Rabbit
                    rabbit={view.rabbit}
                    fourColour={prefs.fourColour}
                    onReveal={(i) => conn.send('rabbit', { index: i })}
                  />
                {:else}
                  <!-- Placeholders for the cards still to come, so the board
                       does not jump about as it is dealt. -->
                  {#each Array(Math.max(0, 5 - board.length)) as _, slot (slot)}
                    <div class="slot"></div>
                  {/each}
                {/if}
              </div>
            {/if}
          {/each}
        </div>

        <div class="felt-name">{structureLabel === 'PLH' ? 'Pot-Limit' : 'No-Limit'} Texas Hold'em</div>
        <div class="felt-mark">{SITE_NAME}</div>
      </div>

      <ul class="seats">
        {#each places as place (place.number)}
          <li style="--x: {place.x}%; --y: {place.y}%">
            <Seat
              seat={bySeat[place.number] ?? null}
              number={place.number}
              isMe={place.number === mySeat}
              isDealer={hand ? hand.button === place.number : view.button === place.number}
              isTurn={hand?.to_act === place.number}
              fourColour={prefs.fourColour}
              bigBlind={settings.big_blind}
              inBigBlinds={prefs.inBigBlinds}
              countdown={actionClockSeat === place.number ? secondsLeft : null}
              countdownOf={clockTotal}
              onBank={view.clock?.phase === 'bank'}
              equity={view.equity?.[String(place.number)] ?? null}
              reaction={reactionBySeat[place.number] ?? null}
              note={myNotes[bySeat[place.number]?.player?.id] ?? ''}
              onNote={mySeat != null
                ? (about, text) => conn.send('note', { about, text })
                : null}
              onSit={sit}
            />
          </li>
        {/each}
      </ul>
    </div>

    {#if view.paused}
      <p class="banner">The table is paused.</p>
    {:else if !hand && view.seats.length < 2}
      <p class="banner">Waiting for another player to sit down.</p>
    {/if}
  </main>

  <!-- top right -->
  <aside class="info">
    <div class="stakes numeric">
      {structureLabel} ~ {settings.small_blind} / {settings.big_blind}
      {#if settings.ante > 0}&nbsp;+{settings.ante}{/if}
    </div>
    <button
      class="sound"
      aria-label={prefs.sound ? 'sound on' : 'sound off'}
      onclick={() => onPrefs({ sound: !prefs.sound })}
    >
      {prefs.sound ? '♪' : '✕'}
    </button>
    <div class="status" data-status={status}>
      {status === 'open' ? 'Connected' : status}
    </div>
  </aside>

  <!-- bottom left -->
  <section class="bottom-left">
    {#if mySeat != null}
      <Reactions onReact={(emoji) => conn.send('react', { emoji })} />
    {/if}
    <button class="log-button" onclick={() => openPanel('log')}>
      LOG / LEDGER<kbd>L</kbd>
    </button>
    <Chat
      bind:this={chat}
      messages={view.messages}
      open={chatOpen}
      onSend={(text) => conn.send('say', { text })}
      onToggle={() => (chatOpen = !chatOpen)}
    />
  </section>

  <!-- bottom right -->
  <section class="bottom-right">
    {#if error}
      <p class="error" role="alert">{error}</p>
    {/if}

    {#if view.run_choice}
      <RunChoice
        runChoice={view.run_choice}
        countdown={view.clock?.kind === 'run_choice' ? secondsLeft : null}
        {mySeat}
        onChoose={(count) => conn.send('choose_runs', { count })}
      />
    {/if}

    {#if view.showdown?.yours}
      <ShowMuck
        cards={myCards}
        countdown={view.clock?.kind === 'showdown' ? secondsLeft : null}
        onShow={(cards) => conn.send('show', { cards })}
        onMuck={() => conn.send('muck', {})}
      />
    {/if}

    {#if mySeat == null}
      {#if onWaitlist}
        <div class="waiting">
          <span
            >Waiting for a seat ({(view.waitlist ?? []).findIndex((w) => w.you) + 1}
            in the queue)</span
          >
          <button onclick={() => conn.send('leave_waitlist', {})}>Cancel</button>
        </div>
      {:else if tableFull}
        <button class="straddle" onclick={() => conn.send('waitlist', {})}>
          Join the waitlist
        </button>
      {:else}
        <p class="hint">Pick a seat to join the game.</p>
      {/if}
    {/if}
    {#if settings.max_straddle > 0 && mySeat != null && !view.next_straddle}
      <button
        class="straddle"
        onclick={() => conn.send('straddle', { amount: settings.big_blind * 2 })}
      >
        Straddle {settings.big_blind * 2} next hand
      </button>
    {:else if view.next_straddle}
      <p class="hint">Straddle of {view.next_straddle.amount} is up next hand.</p>
    {/if}
    <ActionBar
      {legal}
      {myTurn}
      {busy}
      bigBlind={settings.big_blind}
      potTotal={hand?.pot ?? 0}
      inBigBlinds={prefs.inBigBlinds}
      preAction={preAction ? { ...preAction, facing } : { kind: null, facing }}
      onAct={act}
      onPreAction={setPreAction}
    />
  </section>
</div>

<Panel
  {view}
  {conn}
  open={panelOpen}
  tab={panelTab}
  view_hands={hands}
  view_replay={replay}
  view_loading={historyLoading}
  onClose={() => (panelOpen = false)}
/>

<HostPanel {view} {conn} open={hostOpen} onClose={() => (hostOpen = false)} />

{#if optionsOpen}
  <div
    class="scrim"
    role="button"
    tabindex="-1"
    aria-label="close options"
    onclick={() => (optionsOpen = false)}
    onkeydown={(e) => e.key === 'Escape' && (optionsOpen = false)}
  ></div>
  <section class="options" aria-label="options">
    <header>
      <h2>Options</h2>
      <button class="close" onclick={() => (optionsOpen = false)}>&times;</button>
    </header>
    <label>
      <input
        type="checkbox"
        checked={prefs.fourColour}
        onchange={(e) => onPrefs({ fourColour: e.currentTarget.checked })}
      />
      Four-colour deck
    </label>
    <label>
      <input
        type="checkbox"
        checked={prefs.inBigBlinds}
        onchange={(e) => onPrefs({ inBigBlinds: e.currentTarget.checked })}
      />
      Show stacks in big blinds
    </label>
    <label>
      <input
        type="checkbox"
        checked={prefs.sound}
        onchange={(e) => onPrefs({ sound: e.currentTarget.checked })}
      />
      Sounds
    </label>
    <label>
      <input
        type="checkbox"
        checked={prefs.confirmFold}
        onchange={(e) => onPrefs({ confirmFold: e.currentTarget.checked })}
      />
      Ask before folding when checking is free
    </label>
    <label>
      <input
        type="checkbox"
        checked={me?.auto_muck ?? false}
        onchange={(e) => conn.send('auto_muck', { on: e.currentTarget.checked })}
      />
      Muck losing hands automatically
    </label>

    <div class="choice">
      <span class="choice-label">Theme</span>
      <div class="swatches">
        {#each ['dark', 'light'] as name (name)}
          <button
            class:on={prefs.theme === name}
            onclick={() => onPrefs({ theme: name })}
          >
            {name === 'dark' ? 'Dark' : 'Light'}
          </button>
        {/each}
      </div>
    </div>

    <div class="choice">
      <span class="choice-label">Felt</span>
      <div class="swatches">
        {#each FELTS as option (option.id)}
          <button
            class="swatch"
            class:on={prefs.felt === option.id}
            style="background: linear-gradient(160deg, {option.top}, {option.bottom})"
            onclick={() => onPrefs({ felt: option.id })}
            aria-label={option.name}
            title={option.name}
          ></button>
        {/each}
      </div>
    </div>

    <div class="choice">
      <span class="choice-label">Card back</span>
      <div class="swatches">
        {#each CARD_BACKS as option (option.id)}
          <button
            class:on={prefs.cardBack === option.id}
            onclick={() => onPrefs({ cardBack: option.id })}
          >
            {option.name}
          </button>
        {/each}
      </div>
    </div>

    <div class="rejoin">
      <p>
        Your rejoin link gets you back in on another phone. Treat it like a
        password.
      </p>
      <button onclick={() => conn.send('new_rejoin_link')}>
        Show me a new rejoin link
      </button>
    </div>
  </section>
{/if}

<style>
  .screen {
    min-height: 100dvh;
    display: grid;
    grid-template-columns: auto 1fr auto;
    grid-template-rows: auto 1fr auto;
    grid-template-areas:
      'rail main info'
      'rail main .'
      'bl   main br';
    gap: 12px;
    padding: 12px;
  }

  .rail {
    grid-area: rail;
    align-self: center;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .rail button {
    width: 76px;
    min-height: 68px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    padding: 6px;
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-dim);
    background: var(--bg-raised);
  }

  .rail button.on {
    color: var(--amber);
    border-color: var(--amber);
  }

  .glyph {
    font-size: 18px;
  }

  main {
    grid-area: main;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    min-width: 0;
  }

  /* The felt is inset from its column by half a seat plate, so a chair on
     the far left or right still fits on screen instead of pushing the page
     sideways. */
  .table-area {
    position: relative;
    width: min(820px, calc(100% - 130px));
    aspect-ratio: 16 / 9;
    /* The board and the pot are sized against the felt, not the window, so
       five cards always fit on it at any width. */
    container-type: inline-size;
  }

  .felt {
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: radial-gradient(
      ellipse at 50% 34%,
      var(--felt-top),
      var(--felt-bottom) 72%
    );
    box-shadow:
      0 0 0 13px var(--felt-rim),
      0 26px 64px rgba(0, 0, 0, 0.55);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
  }

  .pot-row {
    position: absolute;
    top: 13%;
  }

  .pot {
    padding: 5px 20px;
    border-radius: 999px;
    background: rgba(12, 10, 16, 0.55);
    font-family: var(--numerals);
  }

  .boards {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .board {
    display: flex;
    gap: clamp(3px, 1cqw, 10px);
  }

  .board :global(.card) {
    --w: clamp(26px, 11cqw, 84px);
  }

  .slot {
    width: clamp(26px, 11cqw, 84px);
    aspect-ratio: 5 / 7;
    border-radius: 7px;
    background: rgba(0, 0, 0, 0.12);
  }

  .pot {
    font-size: clamp(15px, 3.4cqw, 26px);
  }

  .felt-name,
  .felt-mark {
    position: absolute;
    color: rgba(255, 255, 255, 0.2);
    letter-spacing: 0.16em;
    text-transform: uppercase;
    font-size: clamp(9px, 1vw, 12px);
    white-space: nowrap;
  }

  .felt-name {
    top: 70%;
  }

  .felt-mark {
    bottom: 7%;
  }

  .seats {
    position: absolute;
    inset: 0;
    margin: 0;
    padding: 0;
    list-style: none;
    /* The list is a full-size overlay, so it would otherwise swallow every
       click aimed at the felt underneath it — the rabbit-hunt cards among
       them. The plates take their own clicks back below. */
    pointer-events: none;
  }

  .seats li {
    position: absolute;
    pointer-events: auto;
    left: var(--x);
    top: var(--y);
    transform: translate(-50%, -50%);
  }

  .info {
    grid-area: info;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 8px;
  }

  .stakes {
    font-family: var(--numerals);
    font-size: 19px;
    color: var(--text-dim);
  }

  .sound {
    width: 42px;
    min-height: 42px;
    padding: 0;
    background: var(--bg-raised);
  }

  .status {
    font-size: 11px;
    color: var(--text-dim);
    text-transform: capitalize;
  }

  .status[data-status='open'] {
    color: var(--green);
  }

  .status[data-status='reconnecting'],
  .status[data-status='waking'] {
    color: var(--amber);
  }

  .bottom-left {
    grid-area: bl;
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-self: end;
  }

  .log-button {
    position: relative;
    align-self: flex-start;
    min-height: 46px;
    padding: 0 18px;
    font-size: 12px;
    letter-spacing: 0.1em;
    background: var(--bg-raised);
  }

  kbd {
    position: absolute;
    top: -8px;
    right: 8px;
    padding: 0 5px;
    font-family: var(--numerals);
    font-size: 10px;
    color: var(--text-dim);
    background: var(--bg);
    border-radius: 4px;
  }

  .bottom-right {
    grid-area: br;
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-self: end;
    align-items: flex-end;
  }

  .error {
    margin: 0;
    color: var(--red);
    font-size: 13px;
    text-align: right;
  }

  .choice {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .choice-label {
    font-size: 13px;
    color: var(--text-dim);
  }

  .swatches {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .swatches button {
    min-height: 36px;
    padding: 0 12px;
    font-size: 13px;
  }

  .swatches button.on {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 22%, transparent);
  }

  .swatches .swatch {
    width: 36px;
    min-height: 36px;
    padding: 0;
    border-radius: 50%;
    border: 2px solid transparent;
  }

  .swatches .swatch.on {
    border-color: var(--text);
  }

  .hint {
    margin: 0;
    color: var(--text-dim);
    font-size: 13px;
  }

  .waiting {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    color: var(--text-dim);
  }

  .waiting button {
    min-height: 36px;
    padding: 0 12px;
    font-size: 13px;
  }

  .straddle {
    min-height: 38px;
    font-size: 12px;
    background: transparent;
    color: var(--amber);
    border-color: color-mix(in srgb, var(--amber) 45%, transparent);
  }

  .banner {
    margin: 0;
    padding: 6px 16px;
    border-radius: 999px;
    background: var(--bg-raised);
    color: var(--text-dim);
    font-size: 13px;
  }

  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(5, 4, 8, 0.6);
    border: 0;
    z-index: 20;
  }

  .options {
    position: fixed;
    z-index: 21;
    left: 16px;
    top: 16px;
    width: min(340px, calc(100vw - 32px));
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 14px;
    background: var(--bg-raised);
    border: 1px solid var(--plate-edge);
    border-radius: var(--radius);
  }

  .options header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .options h2 {
    margin: 0;
    font-size: 15px;
  }

  .options label {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 14px;
    min-height: var(--tap);
  }

  .options input[type='checkbox'] {
    width: 20px;
    height: 20px;
    min-height: 20px;
    accent-color: var(--accent);
  }

  .close {
    width: 36px;
    min-height: 36px;
    padding: 0;
    background: transparent;
    border-color: transparent;
    font-size: 20px;
    color: var(--text-dim);
  }

  .rejoin {
    border-top: 1px solid var(--plate-edge);
    padding-top: 10px;
  }

  .rejoin p {
    margin: 0 0 8px;
    font-size: 12px;
    color: var(--text-dim);
    line-height: 1.5;
  }

  .rejoin button {
    width: 100%;
    font-size: 13px;
  }

  /* ---------------------------------------------------------- phones */
  @media (max-width: 860px) {
    .screen {
      grid-template-columns: 1fr;
      grid-template-rows: auto auto 1fr auto auto;
      grid-template-areas:
        'info'
        'rail'
        'main'
        'br'
        'bl';
      gap: 8px;
      padding: 8px;
    }

    .rail {
      grid-area: rail;
      flex-direction: row;
      justify-content: flex-start;
      gap: 8px;
    }

    .rail button {
      width: auto;
      min-height: 44px;
      flex-direction: row;
      padding: 0 12px;
      font-size: 10px;
    }

    .info {
      flex-direction: row;
      align-items: center;
      justify-content: space-between;
    }

    .stakes {
      font-size: 15px;
    }

    .table-area {
      width: 100%;
      aspect-ratio: 1 / 1.12;
    }

    .felt {
      box-shadow:
        0 0 0 8px var(--felt-rim),
        0 14px 36px rgba(0, 0, 0, 0.5);
    }

    /* The ring of chairs reaches further up and down a portrait felt, so
       the pot and the wordmarks move clear of it. */
    .pot-row {
      top: 30%;
    }

    .felt-name {
      top: 78%;
    }

    .felt-mark {
      display: none;
    }

    .seats li {
      transform: translate(-50%, -50%) scale(0.76);
    }

    .bottom-left,
    .bottom-right {
      align-items: stretch;
    }

    .bottom-right {
      position: sticky;
      bottom: 0;
      padding-bottom: env(safe-area-inset-bottom);
      background: linear-gradient(transparent, var(--bg) 24%);
    }

    .error,
    .hint {
      text-align: left;
    }
  }

  @media (max-width: 420px) {
    .seats li {
      transform: translate(-50%, -50%) scale(0.66);
    }

    .rail button {
      padding: 0 9px;
    }
  }
</style>
