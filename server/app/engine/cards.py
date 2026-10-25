"""Cards as short strings: "As", "Td", "9c".

A card is a two-character string — rank then suit — because that is what
goes into the saved state, the hand history and the log, and a string that
reads as a card is worth more there than two bytes saved.

Rank indexes run 0 (deuce) to 12 (ace); the ace also plays low in the wheel,
which the evaluator handles rather than the card type.
"""

from __future__ import annotations

import secrets

RANKS = "23456789TJQKA"
SUITS = "cdhs"

# How a rank is written out for people: the ten reads as "10", everything
# else is its own character. Keyed by the card's rank character, so callers
# go through RANKS to get there from a rank index.
RANK_NAMES = {rank: rank for rank in RANKS} | {"T": "10"}

DECK: tuple[str, ...] = tuple(rank + suit for rank in RANKS for suit in SUITS)


def rank_of(card: str) -> int:
    """0 for a deuce, 12 for an ace."""
    return RANKS.index(card[0])


def suit_of(card: str) -> str:
    return card[1]


def make_deck() -> list[str]:
    """A fresh deck in order. Shuffling is a separate, deliberate step."""
    return list(DECK)


def shuffled_deck() -> list[str]:
    """One fresh shuffle, from OS entropy.

    `secrets.SystemRandom` draws from the operating system's CSPRNG. It is
    never seeded, its state is never saved, and this is called once per hand
    at the moment the hand starts, so a restart, a redeploy or a new session
    cannot repeat a shuffle (section 4.3).
    """
    deck = make_deck()
    secrets.SystemRandom().shuffle(deck)
    return deck
