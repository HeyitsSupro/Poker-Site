<script>
  /**
   * Shown while the free Render service wakes up (about a minute from cold).
   * It is the first thing most players see at the start of a game night, so
   * it says what is happening rather than spinning silently.
   */
  import { SITE_NAME } from '../config.js';

  let { startedAt = null, status = 'waking', diagnosis = null } = $props();

  let elapsed = $state(0);

  $effect(() => {
    if (startedAt == null) return;
    const tick = () => (elapsed = Math.floor((Date.now() - startedAt) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  });

  let message = $derived(
    status === 'reconnecting'
      ? 'Reconnecting'
      : elapsed < 10
        ? 'Waking up the table'
        : elapsed < 70
          ? 'Still waking up the table'
          : 'Taking longer than usual',
  );
</script>

<div class="screen">
  <div class="mark">{SITE_NAME}</div>

  {#if diagnosis}
    <!-- Nothing has answered for a while and the addresses do not add up,
         so say what is wrong rather than spinning. -->
    <p class="message problem" role="alert">{diagnosis.title}</p>
    <p class="hint">{diagnosis.detail}</p>
    <p class="hint faint">Still trying, in case it comes back.</p>
  {:else}
    <div class="dots" aria-hidden="true"><i></i><i></i><i></i></div>
    <p class="message" role="status">{message}</p>
    <p class="hint">
      {#if elapsed > 70}
        The server is free-tier and may be busy starting. This page keeps trying.
      {:else}
        The server sleeps when nobody is playing. It takes about a minute to
        start, then the table is instant.
      {/if}
    </p>
  {/if}

  {#if elapsed > 3}
    <p class="elapsed numeric">{elapsed}s</p>
  {/if}
</div>

<style>
  .screen {
    min-height: 100dvh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    padding: 24px;
    text-align: center;
  }

  .mark {
    font-size: 26px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--accent);
  }

  .dots {
    display: flex;
    gap: 8px;
  }

  .dots i {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--accent);
    opacity: 0.35;
    animation: pulse 1.2s ease-in-out infinite;
  }

  .dots i:nth-child(2) {
    animation-delay: 0.15s;
  }

  .dots i:nth-child(3) {
    animation-delay: 0.3s;
  }

  @keyframes pulse {
    0%,
    100% {
      opacity: 0.25;
      transform: translateY(0);
    }
    50% {
      opacity: 1;
      transform: translateY(-3px);
    }
  }

  .message {
    margin: 0;
    font-size: 18px;
  }

  .hint {
    margin: 0;
    max-width: 40ch;
    color: var(--text-dim);
    font-size: 14px;
    line-height: 1.5;
  }

  .hint.faint {
    opacity: 0.6;
    font-size: 13px;
  }

  .message.problem {
    color: var(--amber);
    max-width: 24ch;
  }

  .elapsed {
    margin: 0;
    color: var(--text-dim);
    font-family: var(--numerals);
    font-size: 13px;
  }
</style>
