/**
 * Where the player's secret token lives on this device.
 *
 * Phase 0 keeps it in localStorage, scoped per table, and that is enough to
 * survive closing the tab, the app or the phone. It is deliberately *not*
 * the whole identity story: section 4.5 also wants a personal rejoin link
 * and a host-approved claim, because browsers do clear storage. Those land
 * in phase 1 and reuse this module.
 *
 * Every accessor is wrapped: in a private window, or with site data blocked,
 * localStorage throws rather than returning null.
 */

const KEY = (tableId) => `poker.token.${tableId}`;
const NICK_KEY = 'poker.nick';

function read(key) {
  try {
    return globalThis.localStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function write(key, value) {
  try {
    globalThis.localStorage?.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

export function getToken(tableId) {
  return read(KEY(tableId));
}

export function saveToken(tableId, token) {
  return write(KEY(tableId), token);
}

export function clearToken(tableId) {
  try {
    globalThis.localStorage?.removeItem(KEY(tableId));
  } catch {
    /* nothing to do */
  }
}

export function getNick() {
  return read(NICK_KEY) || '';
}

export function saveNick(nick) {
  return write(NICK_KEY, nick);
}

/** A random id for one action, used for the server's duplicate check. */
export function newActionId() {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID();
  const bytes = new Uint8Array(16);
  c.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}
