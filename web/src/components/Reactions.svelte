<script>
  /**
   * The emoji strip. Tapping one puts it beside your seat for a few
   * seconds, for everyone.
   */
  let { onReact } = $props();

  // Must match REACTIONS on the server, which refuses anything else.
  const EMOJI = ['👍', '😂', '😮', '😍', '😡', '🤔', '🎉', '💀'];

  let open = $state(false);

  function send(emoji) {
    onReact(emoji);
    open = false;
  }
</script>

<div class="reactions">
  {#if open}
    <div class="strip" role="group" aria-label="Reactions">
      {#each EMOJI as emoji (emoji)}
        <button onclick={() => send(emoji)} aria-label="React {emoji}">
          {emoji}
        </button>
      {/each}
    </div>
  {/if}
  <button
    class="toggle"
    onclick={() => (open = !open)}
    aria-expanded={open}
    aria-label="Reactions"
  >
    🙂
  </button>
</div>

<style>
  .reactions {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .strip {
    display: flex;
    gap: 2px;
    padding: 4px;
    border-radius: 999px;
    background: var(--bg-raised);
    border: 1px solid var(--plate-edge);
  }

  .strip button,
  .toggle {
    min-width: 40px;
    min-height: 40px;
    padding: 0;
    border: none;
    background: none;
    font-size: 20px;
    line-height: 1;
    border-radius: 999px;
  }

  .strip button:hover,
  .toggle:hover {
    background: rgba(255, 255, 255, 0.08);
  }

  .toggle {
    border: 1px solid var(--plate-edge);
    font-size: 17px;
  }
</style>
