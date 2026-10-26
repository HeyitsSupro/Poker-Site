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
  // Hands that were turned face up have to stay face up through the pause
  // after the hand, and a run-out is dealt on the client long after the
  // server has settled everything — so for those seconds the live seats
  // carry no cards at all. The finished hand still knows which hands were
  // shown, and the server has already decided who may see them, so the
  // cards are merged back onto the plate they belong to.
  let shownCards = $derived.by(() => {
    const rows = hand?.seats;
    if (!Array.isArray(rows)) return {};
    const shown = {};
    for (const row of rows) if (row.cards) shown[row.seat] = row.cards;
    return shown;
  });

  let bySeat = $derived(
    Object.fromEntries(
      view.seats.map((seat) => {
        const cards = seat.cards ?? shownCards[seat.seat] ?? null;
        return [seat.seat, cards === seat.cards ? seat : { ...seat, cards }];
      }),
    ),
  );
  let allBoards = $derived(hand?.boards?.length ? hand.boards : [hand?.board ?? []]);

  // --- running it more than once ---------------------------------------
  //
  // The server settles every run at once, but dealing them all onto the
  // screen in the same instant is unreadable — you cannot see which board
  // did what. So the boards are laid out one under another and dealt in
  // turn, a street at a time, with a beat between runs and the winner's
  // chips paid before the next one starts. Nothing here changes the
  // result; it only paces how it is shown.
  const RUN_STREET_MS = 1000;
  const RUN_GAP_MS = 2000;
  const RUN_LEAD_MS = 600;

  function commonPrefix(boards) {
    if (!boards.length) return 0;
    let i = 0;
    outer: for (; i < boards[0].length; i += 1) {
      for (const b of boards) if (b[i] !== boards[0][i]) break outer;
    }
    return i;
  }

  let runPlan = $derived.by(() => {
    const boards = allBoards;
    if (boards.length < 2) return null;
    const shared = commonPrefix(boards);
    // How the rest of the board would have come, street by street.
    const streets = shared === 0 ? [3, 1, 1] : shared === 3 ? [1, 1] : [1];
    const steps = [];
    let at = RUN_LEAD_MS;
    for (let run = 0; run < boards.length; run += 1) {
      let upto = shared;
      for (const size of streets) {
        upto += size;
        steps.push({ run, upto, at });
        at += RUN_STREET_MS;
      }
      steps.push({ run, upto, at, settled: true });
      at += RUN_GAP_MS;
    }
    return { shared, steps, runs: boards.length };
  });

  // Milliseconds since the finished hand appeared.
  let runElapsed = $state(0);

  $effect(() => {
    const plan = runPlan;
    if (!plan) return;
    // Re-start whenever a different hand is on screen.
    view.last_hand?.hand_no;
    runElapsed = 0;
    const started = Date.now();
    const id = setInterval(() => {
      runElapsed = Date.now() - started;
    }, 100);
    return () => clearInterval(id);
  });

  /** How much of each board is face up at this moment. */
  let boards = $derived.by(() => {
    const plan = runPlan;
    if (!plan) return allBoards;
    return allBoards.map((board, run) => {
      let upto = plan.shared;
      for (const step of plan.steps) {
        if (step.run === run && step.at <= runElapsed) upto = Math.max(upto, step.upto);
      }
      return board.slice(0, upto);
    });
  });

  /** Runs whose chips have been paid so far. */
  let runsSettled = $derived.by(() => {
    const plan = runPlan;
    if (!plan) return 0;
    return plan.steps.filter((s) => s.settled && s.at <= runElapsed).length;
  });

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

  // Who just won, and how much. The hand stays on screen through the gap
  // before the next deal, so this is what marks the winner in that window
  // — otherwise chips simply change and it is easy to miss what happened.
  let winners = $derived.by(() => {
    const result = view.last_hand?.result;
    if (!result || view.hand?.complete === false) return {};
    const out = {};
    const runs = result.runs ?? [];

    if (runPlan) {
      // Run by run: a board pays its winner before the next one is dealt,
      // so the chips arrive when you watch them being won.
      for (let i = 0; i < Math.min(runsSettled, runs.length); i += 1) {
        for (const pot of runs[i].pots ?? []) {
          for (const [seat, amount] of Object.entries(pot.payout ?? {})) {
            out[Number(seat)] = (out[Number(seat)] ?? 0) + amount;
          }
        }
      }
      return out;
    }

    const refunds = result.refunds ?? {};
    for (const [seat, amount] of Object.entries(result.awards ?? {})) {
      // An uncalled bet comes straight back to whoever made it. That is
      // their own money returned, not winnings: raising 20 into a fold
      // wins nothing, so it must not be counted here.
      const won = amount - (refunds[seat] ?? 0);
      if (won > 0) out[Number(seat)] = won;
    }
    return out;
  });
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
  // Where each chair sits, as a percentage of the felt's box, in
  // clockwise order starting with the viewer at the bottom.
  //
  // The felt is a rounded rectangle, so the chairs sit along four edges
  // rather than on an ellipse. Landscape puts three along the top and
  // three along the bottom with two at each end; portrait turns that a
  // quarter and runs three down each side with two across the top. The
  // extremes sit on the rim itself, because the plates straddle it.
  const LAYOUTS_WIDE = {
    2: [[50, 100], [50, 0]],
    3: [[50, 100], [18, 0], [82, 0]],
    4: [[50, 100], [0, 50], [50, 0], [100, 50]],
    5: [[50, 100], [0, 62], [26, 0], [74, 0], [100, 62]],
    6: [[50, 100], [20, 100], [14, 0], [50, 0], [86, 0], [80, 100]],
    7: [[50, 100], [20, 100], [0, 50], [28, 0], [72, 0], [100, 50], [80, 100]],
    8: [
      [50, 100], [20, 100], [0, 50], [22, 0],
      [50, 0], [78, 0], [100, 50], [80, 100],
    ],
    9: [
      [50, 100], [20, 100], [0, 66], [0, 30], [30, 0],
      [70, 0], [100, 30], [100, 66], [80, 100],
    ],
    10: [
      [50, 100], [20, 100], [0, 66], [0, 30], [22, 0],
      [50, 0], [78, 0], [100, 30], [100, 66], [80, 100],
    ],
  };

  // Portrait keeps its side chairs a little inside the rim: the felt is
  // only a phone wide, so a plate centred on the edge would hang off the
  // screen rather than overlap the felt the way it can in landscape.
  const LAYOUTS_TALL = {
    2: [[50, 100], [50, 0]],
    3: [[50, 100], [13, 40], [87, 40]],
    4: [[50, 100], [13, 55], [50, 0], [87, 55]],
    5: [[50, 100], [13, 66], [13, 24], [87, 24], [87, 66]],
    6: [[50, 100], [13, 70], [13, 34], [50, 0], [87, 34], [87, 70]],
    7: [[50, 100], [13, 72], [13, 42], [26, 0], [74, 0], [87, 42], [87, 72]],
    8: [
      [50, 100], [13, 76], [13, 50], [13, 24],
      [50, 0], [87, 24], [87, 50], [87, 76],
    ],
    9: [
      [50, 100], [13, 78], [13, 52], [13, 26], [33, 0],
      [67, 0], [87, 26], [87, 52], [87, 78],
    ],
    // Ten on a phone is the one count that will not fit three a side
    // with two clean chairs across the bottom: the felt is barely wider
    // than two plates. The tenth sits out at the bottom-right corner and
    // a little above the rim, clear of both the hero and the buttons.
    10: [
      [50, 100], [13, 78], [13, 52], [13, 26], [33, 0],
      [67, 0], [87, 26], [87, 52], [87, 78], [90, 94],
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
    const layout = (portrait ? LAYOUTS_TALL : LAYOUTS_WIDE)[count];

    return Array.from({ length: seatCount }, (_, number) => {
      const slot = (number - anchor + seatCount) % seatCount;
      const [x, y] = layout[slot % layout.length];
      return { number, x, y };
    });
  });

  let facing = $derived.by(() => {
    if (!hand || mySeat == null) return 0;
    const seat = bySeat[mySeat];
    if (!seat) return 0;
    return Math.max(0, (hand.current_bet ?? 0) - (seat.committed ?? 0));
  });

  // A locked-in pre-action clears itself the moment the price changes, so
  // nobody ever calls a raise they did not see — and a plain CHECK stops
  // being available at all once there is something to call.
  $effect(() => {
    if (preAction?.kind === 'call' && preAction.facing !== facing) preAction = null;
    if (preAction?.kind === 'check' && facing > 0) preAction = null;
  });

  // When our turn arrives, play whatever was armed.
  $effect(() => {
    if (!myTurn || !preAction) return;
    const armed = preAction;
    preAction = null;
    const canCheckNow = legal.actions.includes('check');
    if (armed.kind === 'check_fold') {
      act(canCheckNow ? 'check' : 'fold');
    } else if (armed.kind === 'check') {
      // Only ever checks. If the price moved it was cleared above, so
      // reaching here without a check available means something changed
      // in the same instant: do nothing rather than guess.
      if (canCheckNow) act('check');
    } else if (armed.kind === 'fold') {
      act('fold');
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
      // On your turn the keys act. Check and call are separate now, so
      // K never silently calls a bet you had not registered.
      if (key === 'f') {
        event.preventDefault();
        act('fold');
      } else if (key === 'k' && legal.actions.includes('check')) {
        event.preventDefault();
        act('check');
      } else if (key === 'c' && legal.actions.includes('call')) {
        event.preventDefault();
        act('call');
      }
    } else {
      // Off turn the same keys arm the matching pre-action.
      if (key === 'f') {
        event.preventDefault();
        setPreAction('fold');
      } else if (key === 'k' && facing === 0) {
        event.preventDefault();
        setPreAction('check');
      } else if (key === 'c' && facing > 0) {
        event.preventDefault();
        setPreAction('call');
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
      <div class="felt" class:quiet-labels={!!view.rabbit}>
        <div class="pot-row">
          <!-- The pot is what is already in the middle. While a round is
               being bet there is more than that on the table, so the
               larger figure sits above it; once the round is swept in the
               two are the same and only the pot is shown. -->
          {#if (hand?.total ?? 0) > (hand?.pot ?? 0)}
            <div class="total numeric">Total {money(hand.total)}</div>
          {/if}
          <div class="pot numeric">{money(hand?.pot ?? 0)}</div>
        </div>

        <div class="boards" data-runs={boards.length}>
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
                    onRevealAll={() => conn.send('rabbit', {})}
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

          {#if view.rabbit}
            <!-- Without this the undealt cards read as an ordinary board,
                 which is exactly the wrong impression: these are cards the
                 hand never reached. -->
            <p class="rabbit-note">
              Cards that never came
            </p>
          {/if}
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
              won={winners[place.number] ?? 0}
              betAbove={place.y > 70}
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
  /* On a wide screen the controls float over the page and the felt is
     centred in the window, the way a real table is.
     
     They used to be grid columns, which coupled their width to the
     table's: opening the chat or a CALL button appearing on your turn
     resized the middle column and the whole table jumped. Overlays
     cannot do that. The felt's size comes from the viewport alone, so it
     is identical at every moment of a hand.
     
     The narrow layout further down goes back to a stacked grid. */
  .screen {
    position: relative;
    min-height: 100dvh;
    padding: 12px;
  }

  .rail,
  .info,
  .bottom-left,
  .bottom-right {
    position: fixed;
    z-index: 5;
    /* These float over the felt, and their boxes are larger than the
       controls inside them. Without this the empty space around a button
       would swallow clicks meant for a seat underneath. The controls
       themselves take their clicks back on the next rule. */
    pointer-events: none;
  }

  /* :global, because some of these children are other components and
     carry their own scoping class rather than this file's. */
  .rail > :global(*),
  .info > :global(*),
  .bottom-left > :global(*),
  .bottom-right > :global(*) {
    pointer-events: auto;
  }

  .rail {
    /* Up at the top-left, clear of the felt. */
    top: 14px;
    left: 14px;
  }

  .info {
    top: 14px;
    right: 18px;
  }

  .bottom-left {
    left: 14px;
    bottom: 14px;
    width: 390px;
  }

  .bottom-right {
    right: 18px;
    bottom: 14px;
    width: 470px;
  }

  @media (max-width: 1200px) {
    .bottom-left {
      width: 320px;
    }

    .bottom-right {
      width: 400px;
    }
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
    min-height: 100dvh;
    /* Room at the bottom for the floating chat and action area, so the
       felt is centred in what is left rather than hidden behind them. */
    padding-bottom: 150px;
    /* Room above for the plates that straddle the rim. Not at the sides:
       padding there would narrow the felt, and a narrow felt is exactly
       what pushes the bottom chairs into each other. */
    padding-top: 44px;
  }

  /* The felt is inset from its column by half a seat plate, so a chair on
     the far left or right still fits on screen instead of pushing the page
     sideways. */
  .table-area {
    position: relative;
    /* About 60% of the window wide, the proportion a real table has on
       screen, but never wider than the column and never so tall that the
       plates hanging off the top and bottom leave the viewport. The
       height cap is expressed as a width because of the aspect ratio. */
    width: min(62vw, 100%, (100dvh - 330px) * 16 / 9);
    aspect-ratio: 16 / 9;
    /* The board and the pot are sized against the felt, not the window, so
       five cards always fit on it at any width. */
    container-type: inline-size;
  }

  /* A rounded rectangle rather than an oval: the chairs sit along four
     straight edges, which reads as a real card table and leaves the
     corners for the ends of each row instead of wasting them on a curve. */
  .felt {
    position: absolute;
    inset: 0;
    border-radius: clamp(40px, 12cqw, 116px);
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

  /* In the flow above the boards rather than pinned to a percentage of
     the felt: a hand run two or three times stacks that many boards, and
     anything at a fixed height ends up behind them. */
  .pot-row {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    margin-bottom: clamp(10px, 2cqw, 22px);
  }

  .total {
    font-family: var(--numerals);
    font-size: clamp(10px, 1.5cqw, 14px);
    letter-spacing: 0.06em;
    color: rgba(255, 255, 255, 0.6);
    white-space: nowrap;
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

  /* The felt's own wordmarks are decoration; the rabbit note is not, and
     they occupy the same band. The decoration gives way. */
  .quiet-labels .felt-name,
  .quiet-labels .felt-mark {
    opacity: 0;
  }

  .rabbit-note {
    margin: 2px 0 0;
    text-align: center;
    font-size: clamp(9px, 1.1cqw, 12px);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: rgba(255, 255, 255, 0.5);
  }

  /* Several boards share the felt when a hand is run more than once, so
     the cards shrink to fit rather than spilling over the seats. */
  .boards[data-runs='2'] :global(.card),
  .boards[data-runs='2'] .slot {
    --w: clamp(24px, 8cqw, 66px);
    width: var(--w);
  }

  .boards[data-runs='3'] :global(.card),
  .boards[data-runs='3'] .slot,
  .boards[data-runs='4'] :global(.card),
  .boards[data-runs='4'] .slot {
    --w: clamp(20px, 6cqw, 50px);
    width: var(--w);
  }

  .board :global(.card) {
    --w: clamp(26px, 11cqw, 84px);
    /* Each board card turns over as it lands. The flop arrives as three
       new elements at once, so they are staggered below rather than
       appearing as one block. Transform and opacity only, so it stays off
       the main thread; `backwards` holds the start frame through the
       delay instead of flashing the card first. */
    animation: deal 300ms cubic-bezier(0.2, 0.7, 0.3, 1) backwards;
  }

  .board :global(.card:nth-child(2)) {
    animation-delay: 70ms;
  }

  .board :global(.card:nth-child(3)) {
    animation-delay: 140ms;
  }

  @keyframes deal {
    from {
      opacity: 0;
      transform: translateY(-14px) scale(0.86) rotate(-4deg);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }

  /* A card still to come. Barely there on purpose: it only exists to
     stop the board shifting sideways as it is dealt, and should never be
     mistaken for a face-down card. */
  .slot {
    width: clamp(30px, 11cqw, 94px);
    aspect-ratio: 5 / 7;
    border-radius: 8px;
    background: rgba(0, 0, 0, 0.07);
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
    top: 64%;
  }

  /* Both wordmarks sit together above the lower-centre of the felt. That
     strip belongs to the viewer: their plate straddles the bottom rim and
     their chips sit just above it, and decoration must not land there. */
  .felt-mark {
    top: 72%;
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
    /* Same reasoning as the grid columns: a fixed width means opening or
       collapsing the chat cannot move the felt. */
    width: var(--rail-w);
    min-width: var(--rail-w);
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
    /* Clears the viewer's own plate, which hangs below the felt and would
       otherwise sit right under — or on top of — this. */
    margin: 34px 0 0;
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
    .rail,
    .info,
    .bottom-left,
    .bottom-right {
      position: static;
      width: auto;
    }

    main {
      min-height: 0;
      padding-bottom: 0;
    }

    .screen {
      display: grid;
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
      /* Taller on a phone: the long sides now carry three chairs each.
         The height has to be capped as well, or a felt that tall pushes
         the chairs hanging off its bottom edge down behind the action
         buttons. The cap is written as a width because of the ratio. */
      width: min(100%, (100dvh - 450px) / 1.38);
      aspect-ratio: 1 / 1.38;
    }

    .felt {
      border-radius: clamp(28px, 11cqw, 72px);
      box-shadow:
        0 0 0 8px var(--felt-rim),
        0 14px 36px rgba(0, 0, 0, 0.5);
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
