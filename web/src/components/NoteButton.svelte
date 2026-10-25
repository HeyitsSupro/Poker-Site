<script>
  /**
   * The little notes icon beside another player's name.
   *
   * Notes are yours alone: the server only ever sends you your own, and
   * never writes them to the table log.
   */
  let { about, nick, text = '', onSave } = $props();

  let open = $state(false);
  let draft = $state('');

  function start() {
    draft = text;
    open = true;
  }

  function save() {
    onSave(about, draft.trim());
    open = false;
  }
</script>

<button
  class="icon"
  class:has-note={!!text}
  onclick={start}
  aria-label="Your note about {nick}"
  title={text || `Note about ${nick}`}
>
  ✎
</button>

{#if open}
  <div class="scrim" role="presentation" onclick={() => (open = false)}></div>
  <div class="sheet" role="dialog" aria-label="Note about {nick}">
    <label for="note-{about}">Your private note about {nick}</label>
    <textarea
      id="note-{about}"
      bind:value={draft}
      maxlength="200"
      rows="3"
      placeholder="Only you can see this."
    ></textarea>
    <div class="row">
      <button onclick={() => (open = false)}>Cancel</button>
      <button class="save" onclick={save}>Save</button>
    </div>
  </div>
{/if}

<style>
  .icon {
    min-width: 22px;
    min-height: 22px;
    padding: 0;
    border: none;
    background: none;
    font-size: 12px;
    line-height: 1;
    color: var(--text-dim);
    opacity: 0.6;
  }

  .icon:hover,
  .icon:focus-visible {
    opacity: 1;
    color: var(--accent);
  }

  .icon.has-note {
    opacity: 1;
    color: var(--accent);
  }

  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(5, 4, 8, 0.6);
    z-index: 40;
  }

  .sheet {
    position: fixed;
    z-index: 41;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    width: min(360px, calc(100vw - 32px));
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 16px;
    border-radius: var(--radius);
    background: var(--bg-raised);
    border: 1px solid var(--plate-edge);
  }

  label {
    font-size: 13px;
    color: var(--text-dim);
  }

  textarea {
    font: inherit;
    color: inherit;
    width: 100%;
    padding: 10px;
    border-radius: var(--radius);
    border: 1px solid var(--plate-edge);
    background: var(--bg);
    resize: vertical;
  }

  .row {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }

  .save {
    background: var(--accent);
    border-color: transparent;
    color: #1a1020;
    font-weight: 650;
  }
</style>
