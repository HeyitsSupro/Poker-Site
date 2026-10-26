<script>
  /** Table chat: the last few messages, collapsible, with a chevron. */
  let { messages = [], open = true, onSend, onToggle } = $props();

  let text = $state('');
  let field = $state(null);
  let list = $state(null);

  // Keep the newest message in view, but only when the reader is already
  // at the bottom — scrolling back to re-read something should not be
  // yanked away every time somebody types.
  $effect(() => {
    messages;
    if (!list) return;
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 60;
    if (atBottom) queueMicrotask(() => list && (list.scrollTop = list.scrollHeight));
  });

  export function focusField() {
    field?.focus();
  }

  function colourFor(nick) {
    // A stable colour per nickname, so people are recognisable in the log.
    let hash = 0;
    for (const ch of nick) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
    return `hsl(${hash} 70% 72%)`;
  }

  function send(event) {
    event.preventDefault();
    const value = text.trim();
    if (!value) return;
    onSend(value);
    text = '';
  }

  function time(at) {
    if (!at) return '';
    const date = new Date(at);
    return Number.isNaN(date.getTime())
      ? ''
      : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
</script>

<div class="chat" class:closed={!open}>
  <button class="toggle" onclick={onToggle} aria-expanded={open}>
    <span class="chevron" class:up={!open}>&#8964;</span>
    Chat
  </button>

  {#if open}
    <ul bind:this={list}>
      {#each messages as message (message.id)}
        <li>
          <b style="color: {colourFor(message.nick)}">{message.nick}</b>
          <span>{message.text}</span>
          <time>{time(message.at)}</time>
        </li>
      {/each}
    </ul>
    <form onsubmit={send}>
      <input
        bind:this={field}
        bind:value={text}
        maxlength="300"
        placeholder="Say something"
        aria-label="chat message"
      />
      <button type="submit" disabled={!text.trim()}>Send</button>
    </form>
  {/if}
</div>

<style>
  .chat {
    display: flex;
    flex-direction: column;
    gap: 6px;
    width: 100%;
  }

  .toggle {
    align-self: flex-start;
    min-height: 34px;
    padding: 0 12px;
    font-size: 12px;
    background: transparent;
    border-color: transparent;
    color: var(--text-dim);
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .chevron {
    display: inline-block;
    transition: transform 140ms ease;
  }

  .chevron.up {
    transform: rotate(180deg);
  }

  /* A fixed height with its own scroll: the whole history is here to
     read, and the panel never changes size as messages arrive — which
     would otherwise push the rest of the page about. */
  ul {
    margin: 0;
    padding: 6px 10px;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 5px;
    font-size: 14px;
    height: 158px;
    overflow-y: auto;
    overscroll-behavior: contain;
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--bg-raised) 72%, transparent);
    border: 1px solid var(--plate-edge);
    scrollbar-width: thin;
  }

  li {
    display: flex;
    gap: 6px;
    align-items: baseline;
    /* A long message wraps instead of running off to the right and
       pushing everything else out of the panel. */
    flex-wrap: wrap;
    overflow-wrap: anywhere;
    word-break: break-word;
  }

  li span {
    flex: 1 1 auto;
    min-width: 0;
  }

  li span {
    color: var(--text-dim);
    overflow-wrap: anywhere;
  }

  b {
    font-weight: 600;
    flex: none;
  }

  time {
    margin-left: auto;
    font-size: 10px;
    color: #6b6577;
    flex: none;
  }

  form {
    display: flex;
    gap: 6px;
  }

  form input {
    min-height: 40px;
  }

  form button {
    min-height: 40px;
    flex: none;
    font-size: 13px;
  }
</style>
