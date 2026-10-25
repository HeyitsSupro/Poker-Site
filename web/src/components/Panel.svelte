<script>
  import History from './History.svelte';
  /**
   * The LOG / LEDGER panel. Everyone at the table can open both tabs and
   * download either, the way PokerNow does — the ledger is the thing people
   * settle up from, so it should not be the host's private view.
   *
   * On a phone it comes up as a bottom sheet.
   */
  let {
    view,
    open = false,
    tab = 'log',
    conn,
    // Hand history is a reply to a request rather than part of the table
    // state, so it is passed in alongside the view.
    view_hands = [],
    view_replay = null,
    view_loading = false,
    onClose,
  } = $props();

  // Which tab is showing. It follows `tab` when the panel is opened from a
  // button, and then the person can switch freely.
  let current = $state('log');

  $effect(() => {
    current = tab;
  });

  function money(value) {
    return (value ?? 0).toLocaleString();
  }

  function describe(entry) {
    const who = entry.player_id ? nickOf(entry.player_id) : '';
    switch (entry.kind) {
      case 'joined':
        return `${entry.nick} joined`;
      case 'sat_down':
        return `${who} sat down in seat ${entry.seat + 1}`;
      case 'stood_up':
        return `${who} stood up (${money(entry.held)} held)`;
      case 'bought_in':
        return `${who} bought in for ${money(entry.amount)}`;
      case 'resumed':
        return `${who} resumed ${money(entry.amount)} held`;
      case 'resumed_cash_out':
        return `${who} came back with ${money(entry.amount)} cashed out`;
      case 'cashed_out':
        return `${who} cashed out ${money(entry.amount)}`;
      case 'hand_started':
        return `--- hand #${entry.hand_no} ---`;
      case 'hand_complete':
        return `hand #${entry.hand_no} finished`;
      case 'action':
        return `${who} ${entry.action}${entry.amount ? ` to ${money(entry.amount)}` : ''}`;
      case 'away':
        return `${who} is away`;
      case 'back':
        return `${who} is back`;
      case 'paused':
        return 'the table was paused';
      case 'resumed_table':
        return 'the table resumed';
      case 'device_changed':
        return `${who} switched device`;
      default:
        return entry.kind.replace(/_/g, ' ');
    }
  }

  function nickOf(playerId) {
    const seat = view.seats.find((s) => s.player.id === playerId);
    if (seat) return seat.player.nick;
    const row = view.ledger.find((r) => r.player_id === playerId);
    return row ? row.nick : 'someone';
  }

  function download(name, text, type) {
    const blob = new Blob([text], { type });
    const url = URL.createObjectURL(blob);
    const link = Object.assign(document.createElement('a'), {
      href: url,
      download: name,
    });
    link.click();
    URL.revokeObjectURL(url);
  }

  function downloadLog() {
    const lines = view.log.map((e) => `${e.at}\t${describe(e)}`);
    download('log.txt', lines.join('\n'), 'text/plain');
  }

  function downloadLedgerCsv() {
    const header = 'player,buy_in_total,buy_in_count,cash_out,held,seated,net';
    const rows = view.ledger.map((r) =>
      [
        `"${r.nick.replace(/"/g, '""')}"`,
        r.buy_in_total,
        r.buy_in_count,
        r.cash_out_total,
        r.held,
        r.seated,
        r.net,
      ].join(','),
    );
    download('ledger.csv', [header, ...rows].join('\n'), 'text/csv');
  }

  function downloadTracker() {
    // The owner's own tracker format, one row per player:
    //   | <buy-in> | <count> | <pnl> | <notes> |
    const rows = (view.tracker ?? []).map(
      (r) => `| ${r.buy_in} | ${r.count} | ${r.pnl} | ${r.notes} |`,
    );
    download('session-summary.md', rows.join('\n') + '\n', 'text/markdown');
  }

  function downloadLedgerMarkdown() {
    // The owner's tracker format: | buy-in | count | pnl | notes |
    const rows = view.ledger.map(
      (r) => `| ${r.nick} | ${r.buy_in_total} | ${r.buy_in_count} | ${r.net} |  |`,
    );
    const text = [
      '| player | buy-in | count | pnl | notes |',
      '| --- | --- | --- | --- | --- |',
      ...rows,
    ].join('\n');
    download('ledger.md', text, 'text/markdown');
  }
</script>

{#if open}
  <div
    class="scrim"
    role="button"
    tabindex="-1"
    aria-label="close"
    onclick={onClose}
    onkeydown={(e) => e.key === 'Escape' && onClose()}
  ></div>
  <section class="panel" aria-label="log and ledger">
    <header>
      <div class="tabs">
        <button class:on={current === 'log'} onclick={() => (current = 'log')}>
          Log
        </button>
        <button class:on={current === 'ledger'} onclick={() => (current = 'ledger')}>
          Ledger
        </button>
        <button
          class:on={current === 'history'}
          onclick={() => {
            current = 'history';
            conn.send_history({});
          }}
        >
          Hands
        </button>
      </div>
      <button class="close" onclick={onClose} aria-label="close">&times;</button>
    </header>

    {#if current === 'log'}
      <div class="body">
        <ul class="log">
          {#each [...view.log].reverse() as entry, index (index)}
            <li>{describe(entry)}</li>
          {/each}
        </ul>
      </div>
      <footer>
        <button onclick={downloadLog}>Download log</button>
      </footer>
    {:else if current === 'history'}
      <div class="body">
        <History
          {conn}
          hands={view_hands}
          replay={view_replay}
          loading={view_loading}
          fourColour={false}
        />
      </div>
    {:else}
      <div class="body">
        <table>
          <thead>
            <tr>
              <th>Player</th>
              <th>Buy-in</th>
              <th>#</th>
              <th>Held</th>
              <th>Stack</th>
              <th>Net</th>
            </tr>
          </thead>
          <tbody>
            {#each view.ledger as row (row.player_id)}
              <tr>
                <td>{row.nick}</td>
                <td class="numeric">{money(row.buy_in_total)}</td>
                <td class="numeric">{row.buy_in_count}</td>
                <td class="numeric">{money(row.held)}</td>
                <td class="numeric">{money(row.seated)}</td>
                <td class="numeric" class:up={row.net > 0} class:down={row.net < 0}>
                  {row.net > 0 ? '+' : ''}{money(row.net)}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <footer>
        <button onclick={downloadLedgerCsv}>CSV</button>
        <button onclick={downloadLedgerMarkdown}>Markdown</button>
        <button onclick={downloadTracker}>Session summary</button>
      </footer>
    {/if}
  </section>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(5, 4, 8, 0.6);
    border: 0;
    z-index: 20;
  }

  .panel {
    position: fixed;
    z-index: 21;
    left: 16px;
    bottom: 16px;
    width: min(460px, calc(100vw - 32px));
    max-height: min(70vh, 560px);
    display: flex;
    flex-direction: column;
    background: var(--bg-raised);
    border: 1px solid var(--plate-edge);
    border-radius: var(--radius);
    overflow: hidden;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 8px 8px 12px;
    border-bottom: 1px solid var(--plate-edge);
  }

  .tabs {
    display: flex;
    gap: 6px;
  }

  .tabs button {
    min-height: 36px;
    padding: 0 14px;
    font-size: 13px;
    background: transparent;
    border-color: transparent;
    color: var(--text-dim);
  }

  .tabs button.on {
    color: var(--text);
    border-color: var(--plate-edge);
    background: var(--plate);
  }

  .close {
    min-height: 36px;
    width: 36px;
    padding: 0;
    background: transparent;
    border-color: transparent;
    font-size: 20px;
    color: var(--text-dim);
  }

  .body {
    overflow: auto;
    padding: 10px 14px;
    -webkit-overflow-scrolling: touch;
  }

  .log {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 5px;
    font-size: 13px;
    color: var(--text-dim);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }

  th {
    text-align: left;
    font-weight: 500;
    color: var(--text-dim);
    padding-bottom: 6px;
  }

  td {
    padding: 5px 0;
    border-top: 1px solid rgba(255, 255, 255, 0.05);
  }

  .numeric {
    text-align: right;
    font-family: var(--numerals);
  }

  .up {
    color: var(--green);
  }
  .down {
    color: var(--red);
  }

  footer {
    display: flex;
    gap: 8px;
    padding: 10px 14px;
    border-top: 1px solid var(--plate-edge);
  }

  footer button {
    min-height: 38px;
    font-size: 13px;
    flex: 1;
  }

  @media (max-width: 720px) {
    .panel {
      left: 0;
      right: 0;
      bottom: 0;
      width: 100%;
      max-height: 78vh;
      border-radius: var(--radius) var(--radius) 0 0;
      padding-bottom: env(safe-area-inset-bottom);
    }
  }
</style>
