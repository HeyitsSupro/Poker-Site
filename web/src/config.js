/**
 * Reads the runtime config in public/config.js.
 *
 * Runtime rather than build-time so the backend address can change without
 * reinstalling Node and rebuilding: edit docs/config.js (or web/public/config.js
 * and rebuild) and commit.
 */
const raw = globalThis.window?.POKER_CONFIG ?? {};

export const SITE_NAME = raw.SITE_NAME || 'Felt';

export const BACKEND_URL = (raw.BACKEND_URL || 'http://localhost:8000').replace(/\/+$/, '');

export function wsUrl(tableId) {
  const base = BACKEND_URL.replace(/^http/, 'ws');
  return `${base}/ws?table=${encodeURIComponent(tableId)}`;
}

export function healthUrl() {
  return `${BACKEND_URL}/healthz`;
}

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '0.0.0.0']);

/** Where this page is served from, in the form a server's allow-list wants. */
export function pageOrigin() {
  return typeof location === 'undefined' ? '' : location.origin;
}

/**
 * Why the backend might be unreachable from *this* device.
 *
 * A server that is merely asleep and a server address that is plain wrong
 * look exactly the same from the browser: the request fails either way.
 * The difference matters enormously to whoever set the site up, so when
 * the page cannot get through this works out the likely reason from the
 * two addresses involved and says so, rather than spinning forever.
 *
 * Returns null when nothing is obviously misconfigured — in which case the
 * server really may just be starting.
 */
export function configProblem() {
  if (typeof location === 'undefined') return null;

  let backend;
  try {
    backend = new URL(BACKEND_URL);
  } catch {
    return {
      title: 'The server address is not a valid URL',
      detail: `BACKEND_URL is set to "${BACKEND_URL}". Fix it in docs/config.js.`,
    };
  }

  const pageIsLocal = LOCAL_HOSTS.has(location.hostname);
  const backendIsLocal = LOCAL_HOSTS.has(backend.hostname);

  // The classic: the site is shared with friends, but the server address
  // still points at the machine it was developed on. On anybody else's
  // device "localhost" means their own device, where nothing is running.
  if (backendIsLocal && !pageIsLocal) {
    return {
      title: 'This device cannot reach the server',
      detail:
        `The site is set to talk to ${BACKEND_URL}, and "${backend.hostname}" ` +
        'means "this device", so it only ever works on the computer running ' +
        'the server. Set BACKEND_URL in docs/config.js to the public address ' +
        'of the backend.',
    };
  }

  // A secure page may not call an insecure address; the browser blocks it
  // before it reaches the network.
  if (location.protocol === 'https:' && backend.protocol === 'http:') {
    return {
      title: 'The server address is not secure',
      detail:
        `This page is served over HTTPS, so the browser refuses to call ` +
        `${BACKEND_URL} over plain HTTP. Change BACKEND_URL in docs/config.js ` +
        'to the https:// address.',
    };
  }

  return null;
}

export const PROTOCOL_VERSION = 1;
