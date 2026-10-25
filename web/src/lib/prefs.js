/**
 * Preferences that live on this device: theme, deck style, sounds, whether
 * stacks are shown in chips or big blinds. They are a convenience, not
 * state the game depends on, so a browser that refuses storage just gets
 * the defaults.
 */

const KEY = 'poker.prefs';

export const DEFAULTS = {
  sound: true,
  fourColour: false,
  inBigBlinds: false,
  confirmFold: false,
  // Dark is the default (section 8.5); light is there for a bright room.
  theme: 'dark',
  felt: 'purple',
  cardBack: 'web',
};

export const FELTS = [
  { id: 'purple', name: 'Purple', top: '#8d4ab6', bottom: '#5e2a80' },
  { id: 'green', name: 'Green', top: '#2f8f63', bottom: '#1b5a3e' },
  { id: 'blue', name: 'Blue', top: '#3a6fb5', bottom: '#223f73' },
  { id: 'slate', name: 'Slate', top: '#5a6072', bottom: '#343846' },
  { id: 'crimson', name: 'Crimson', top: '#a8454f', bottom: '#6d242c' },
];

export const CARD_BACKS = [
  { id: 'web', name: 'Web' },
  { id: 'lattice', name: 'Lattice' },
  { id: 'plain', name: 'Plain' },
];

/**
 * Push the look-and-feel preferences onto the document, where the CSS can
 * see them. Kept here so there is one place that knows how a preference
 * becomes a custom property.
 */
export function applyPrefs(prefs) {
  const root = globalThis.document?.documentElement;
  if (!root) return;
  root.dataset.theme = prefs.theme === 'light' ? 'light' : 'dark';
  root.dataset.cardBack = prefs.cardBack ?? 'web';
  const felt = FELTS.find((f) => f.id === prefs.felt) ?? FELTS[0];
  root.style.setProperty('--felt-top', felt.top);
  root.style.setProperty('--felt-bottom', felt.bottom);
}

export function loadPrefs() {
  try {
    const raw = globalThis.localStorage?.getItem(KEY);
    return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : { ...DEFAULTS };
  } catch {
    return { ...DEFAULTS };
  }
}

export function savePrefs(prefs) {
  try {
    globalThis.localStorage?.setItem(KEY, JSON.stringify(prefs));
  } catch {
    /* a private window; the defaults are fine */
  }
}
