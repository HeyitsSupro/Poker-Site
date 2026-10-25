/**
 * Card faces. Our own artwork: a rank, a suit glyph, nothing borrowed.
 *
 * Cards arrive from the server as two characters, rank then suit, e.g.
 * "As" or "Td".
 */

const SUITS = {
  s: { glyph: '♠', name: 'spades' },
  h: { glyph: '♥', name: 'hearts' },
  d: { glyph: '♦', name: 'diamonds' },
  c: { glyph: '♣', name: 'clubs' },
};

const RANK_LABELS = { T: '10' };

export function parseCard(card) {
  if (typeof card !== 'string' || card.length !== 2) return null;
  const [rank, suit] = card;
  const info = SUITS[suit];
  if (!info) return null;
  return {
    code: card,
    rank,
    label: RANK_LABELS[rank] ?? rank,
    suit,
    glyph: info.glyph,
    name: `${RANK_LABELS[rank] ?? rank} of ${info.name}`,
  };
}

/**
 * Two-colour is the default: spades and clubs black, hearts and diamonds
 * red. Four-colour gives diamonds and clubs their own, which some people
 * find much easier to read at a glance.
 */
export function suitColour(suit, fourColour) {
  if (!fourColour) return suit === 'h' || suit === 'd' ? 'red' : 'black';
  return { s: 'black', h: 'red', d: 'blue', c: 'green' }[suit] ?? 'black';
}
