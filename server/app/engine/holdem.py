"""The Texas Hold'em engine.

Pure: no network, no database, no clock of its own. A hand is a plain data
structure plus functions that move it forward, which is what makes it
testable on its own, fuzzable over a hundred thousand hands, and safe to
freeze to the database between any two actions.

The shape is deliberately general in the places section 7 says variants will
arrive: how many hole cards are dealt, what the board looks like, which
evaluator ranks a hand, and what caps a raise are each decided in one place.
Hold'em is the only thing wired up today.

Nothing here reaches for randomness. The deck is shuffled once when the hand
starts and then lives in the state, so restoring a saved hand deals exactly
the cards that were always going to come.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from . import pots as pot_math
from .cards import shuffled_deck
from .evaluator import best_five, describe, evaluate
from .limits import Situation, allowed_range, preflop_cap, structure_limit

PREFLOP = "preflop"
FLOP = "flop"
TURN = "turn"
RIVER = "river"
# Betting is over, someone is all in and board cards are still to come, so
# the players with a say are choosing how many times to run it (section 6.6).
RUN_CHOICE = "run_choice"
SHOWDOWN = "showdown"
COMPLETE = "complete"

STREETS = (PREFLOP, FLOP, TURN, RIVER)
# How many board cards each street adds, and whether a card is burned first.
BOARD_PLAN = {FLOP: 3, TURN: 1, RIVER: 1}

HOLE_CARDS = 2


class IllegalAction(Exception):
    """The action is not allowed. The message is shown to the player."""


@dataclass
class Seat:
    seat: int
    player_id: str
    stack: int
    hole: list[str] = field(default_factory=list)
    # Put in on the current street.
    committed: int = 0
    # Put in across the whole hand.
    total: int = 0
    folded: bool = False
    all_in: bool = False
    # Has acted since the last full bet or raise.
    acted: bool = False
    # Chose to show at showdown (set by the table layer).
    revealed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "player_id": self.player_id,
            "stack": self.stack,
            "hole": list(self.hole),
            "committed": self.committed,
            "total": self.total,
            "folded": self.folded,
            "all_in": self.all_in,
            "acted": self.acted,
            "revealed": self.revealed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Seat:
        return cls(
            seat=data["seat"],
            player_id=data["player_id"],
            stack=data["stack"],
            hole=list(data.get("hole", [])),
            committed=data.get("committed", 0),
            total=data.get("total", 0),
            folded=data.get("folded", False),
            all_in=data.get("all_in", False),
            acted=data.get("acted", False),
            revealed=data.get("revealed", False),
        )


@dataclass(frozen=True)
class Config:
    """Everything the host's settings contribute to one hand."""

    small_blind: int = 1
    big_blind: int = 2
    ante: int = 0
    # "none", "all" (everyone antes) or "big-blind" (the big blind posts for
    # everyone). Antes are dead money: they go to the pot and do not count
    # towards the amount to call.
    ante_type: str = "none"
    structure: str = "no-limit"  # or "pot-limit"
    preflop_limit: int = 0  # 0 = off
    # {"seat": int, "amount": int} when someone straddled.
    straddle: dict[str, int] | None = None
    runs: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "small_blind": self.small_blind,
            "big_blind": self.big_blind,
            "ante": self.ante,
            "ante_type": self.ante_type,
            "structure": self.structure,
            "preflop_limit": self.preflop_limit,
            "straddle": self.straddle,
            "runs": self.runs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


@dataclass
class Hand:
    hand_no: int
    config: Config
    button: int
    seats: dict[int, Seat]
    # Seat numbers in clockwise order, starting at the button.
    order: list[int]
    deck: list[str]
    street: str = PREFLOP
    board: list[str] = field(default_factory=list)
    burned: list[str] = field(default_factory=list)
    # Completions dealt after betting ended, one per run.
    run_boards: list[list[str]] = field(default_factory=list)
    current_bet: int = 0
    # The size of the last full bet or raise: the minimum-raise increment.
    min_raise: int = 0
    to_act: int | None = None
    last_aggressor: int | None = None
    log: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    # What everyone had before a chip moved, so conservation can be checked.
    starting_stacks: dict[int, int] = field(default_factory=dict)
    # Run-it-more: how many runs each player with a say has asked for.
    run_choices: dict[int, int] = field(default_factory=dict)
    # The most this hand could be run, given the host's setting and the
    # cards left in the deck.
    max_runs: int = 1
    # Which betting street we had reached when the run choice opened, so
    # the right board cards are dealt once it is settled.
    pending_street: str = RIVER
    # Seats that reached showdown without having to turn their hand over:
    # they may show or muck as they please (section 6.8). The table layer
    # collects the answers; the engine only works out who gets the choice.
    show_choice: list[int] = field(default_factory=list)

    # ------------------------------------------------------------ helpers

    def seat_order_from(self, start_after: int) -> list[int]:
        """Seats clockwise, starting with the one after `start_after`.

        `start_after` need not be a seat in the hand: the button can sit on
        an empty chair (the dead button, section 6.9), and the order still
        has to come out right.
        """
        later = [s for s in self.order if s > start_after]
        earlier = [s for s in self.order if s <= start_after]
        return later + earlier

    @property
    def order_from_button(self) -> list[int]:
        """Seats from the first one left of the button: odd-chip order."""
        return self.seat_order_from(self.button)

    def live(self) -> list[int]:
        """Seats still in the hand (not folded)."""
        return [s for s in self.order if not self.seats[s].folded]

    def actors(self) -> list[int]:
        """Seats that can still make a decision."""
        return [
            s for s in self.order if not self.seats[s].folded and not self.seats[s].all_in
        ]

    @property
    def pot_total(self) -> int:
        return sum(seat.total for seat in self.seats.values())

    def chips_on_table(self) -> int:
        """Every chip in the game, wherever it currently sits.

        While a hand runs that is the stacks plus the middle; once it is
        paid out the middle is empty and it is just the stacks. This should
        equal `sum(starting_stacks.values())` at every single moment, and
        the fuzz tests check exactly that after every action.
        """
        stacks = sum(seat.stack for seat in self.seats.values())
        if self.street == COMPLETE:
            return stacks
        return stacks + self.pot_total

    def note(self, kind: str, **payload: Any) -> None:
        self.log.append({"kind": kind, **payload})

    # ------------------------------------------------------------- betting

    def situation(self, seat_no: int) -> Situation:
        seat = self.seats[seat_no]
        return Situation(
            street=self.street,
            current_bet=self.current_bet,
            pot_total=self.pot_total,
            to_call=max(0, self.current_bet - seat.committed),
            committed=seat.committed,
            stack=seat.stack,
        )

    def limits(self) -> list[tuple[str, Any]]:
        """Every cap currently switched on. The tightest one wins."""
        active = [("structure", structure_limit(self.config.structure))]
        if self.config.preflop_limit > 0:
            active.append(("preflop limit", preflop_cap(self.config.preflop_limit)))
        return active

    def legal_actions(self, seat_no: int) -> dict[str, Any]:
        """What this player may do, and the raise range they may do it in.

        The client renders this; it never works the rules out for itself.
        """
        if self.to_act != seat_no or self.street not in STREETS:
            return {"actions": [], "to_call": 0}

        seat = self.seats[seat_no]
        to_call = max(0, self.current_bet - seat.committed)
        actions: list[str] = ["fold"]

        if to_call == 0:
            actions.append("check")
        else:
            actions.append("call")

        band = allowed_range(self.situation(seat_no), self.min_raise, self.limits())
        if not self._may_reopen(seat):
            band = replace(band, can_raise=False)
        info: dict[str, Any] = {
            "actions": actions,
            "to_call": min(to_call, seat.stack),
            "call_is_all_in": to_call >= seat.stack,
            "pot_total": self.pot_total,
            "current_bet": self.current_bet,
        }

        if band.can_raise:
            actions.append("raise" if self.current_bet > 0 else "bet")
            info["min_raise_to"] = band.min_raise_to
            info["max_raise_to"] = band.max_raise_to
            info["raise_is_all_in_only"] = band.all_in_only
            if band.capped_by and band.max_raise_to < seat.committed + seat.stack:
                info["capped_by"] = band.capped_by
        return info

    def act(self, seat_no: int, action: str, amount: int | None = None) -> None:
        """Apply one player action. Raises IllegalAction with a reason."""
        if self.street not in STREETS:
            raise IllegalAction("The hand is not taking actions right now.")
        if self.to_act != seat_no:
            raise IllegalAction("It is not your turn.")

        seat = self.seats[seat_no]
        to_call = max(0, self.current_bet - seat.committed)

        if action == "fold":
            seat.folded = True
            seat.acted = True
            self.note("fold", seat=seat_no)

        elif action == "check":
            if to_call > 0:
                raise IllegalAction("You cannot check facing a bet.")
            seat.acted = True
            self.note("check", seat=seat_no)

        elif action == "call":
            if to_call == 0:
                raise IllegalAction("There is nothing to call.")
            paid = self._commit(seat, min(to_call, seat.stack))
            seat.acted = True
            self.note("call", seat=seat_no, amount=paid, all_in=seat.all_in)

        elif action in ("bet", "raise"):
            if amount is None:
                raise IllegalAction("A bet needs an amount.")
            self._raise_to(seat, int(amount))

        else:
            raise IllegalAction(f"Unknown action {action!r}.")

        self._after_action()

    def _commit(self, seat: Seat, amount: int) -> int:
        amount = max(0, min(amount, seat.stack))
        seat.stack -= amount
        seat.committed += amount
        seat.total += amount
        if seat.stack == 0:
            seat.all_in = True
        return amount

    def _may_reopen(self, seat: Seat) -> bool:
        """May this player raise, or only call and fold?

        A player who has already acted on this street and is now facing only
        a short all-in does not get a fresh raise (section 6.9). Every full
        bet or raise clears `acted` for everyone else, so "has not acted
        since the last full raise" is exactly `not seat.acted`.
        """
        return not seat.acted

    def _raise_to(self, seat: Seat, target: int) -> None:
        band = allowed_range(self.situation(seat.seat), self.min_raise, self.limits())
        all_in_to = seat.committed + seat.stack

        if not self._may_reopen(seat):
            raise IllegalAction(
                "An all-in for less than a full raise does not reopen the "
                "betting; you may call or fold."
            )
        if not band.can_raise:
            raise IllegalAction("You cannot raise here; you may call or fold.")
        if target < band.min_raise_to:
            if target == all_in_to and all_in_to > self.current_bet:
                pass  # all in for less than a full raise: always allowed
            else:
                raise IllegalAction(
                    f"The smallest raise here is {band.min_raise_to}."
                )
        if target > band.max_raise_to:
            reason = (
                f" ({band.capped_by})" if band.capped_by else ""
            )
            raise IllegalAction(f"The most you can raise to here is {band.max_raise_to}{reason}.")

        opening = self.current_bet == 0
        increase = target - self.current_bet
        is_full_raise = increase >= self.min_raise
        self._commit(seat, target - seat.committed)

        if is_full_raise:
            # A full raise reopens the betting: everyone must act again.
            self.min_raise = increase
            for other in self.seats.values():
                if other.seat != seat.seat and not other.folded and not other.all_in:
                    other.acted = False
            self.last_aggressor = seat.seat
        else:
            # A short all-in raises the amount to call but does not give
            # players who have already acted a fresh decision (section 6.9).
            self.note("short_all_in", seat=seat.seat, to=target)

        self.current_bet = max(self.current_bet, target)
        seat.acted = True
        self.note(
            "bet" if opening else "raise",
            seat=seat.seat,
            to=target,
            all_in=seat.all_in,
        )

    # ------------------------------------------------------------ sequencing

    def _next_to_act(self, after: int) -> int | None:
        for seat_no in self.seat_order_from(after):
            seat = self.seats[seat_no]
            if seat.folded or seat.all_in:
                continue
            if not seat.acted or seat.committed < self.current_bet:
                return seat_no
        return None

    def _after_action(self) -> None:
        if len(self.live()) <= 1:
            self._finish()
            return

        nxt = self._next_to_act(self.to_act)
        if nxt is not None:
            self.to_act = nxt
            return

        self._close_street()

    def _close_street(self) -> None:
        for seat in self.seats.values():
            seat.committed = 0
            seat.acted = False
        self.current_bet = 0
        self.min_raise = self.config.big_blind
        self.last_aggressor = None

        if len(self.actors()) <= 1 and self._someone_is_all_in():
            # No more betting is possible: the rest of the board just runs.
            self._run_out()
            return

        index = STREETS.index(self.street)
        if index + 1 >= len(STREETS):
            self._run_out()
            return

        self.street = STREETS[index + 1]
        self._deal_street(self.street)

        self.to_act = self._first_to_act_postflop()
        if self.to_act is None:
            self._close_street()

    def _someone_is_all_in(self) -> bool:
        return any(s.all_in for s in self.seats.values() if not s.folded)

    def _first_to_act_postflop(self) -> int | None:
        for seat_no in self.order_from_button:
            seat = self.seats[seat_no]
            if not seat.folded and not seat.all_in:
                return seat_no
        return None

    def _deal_street(self, street: str) -> None:
        count = BOARD_PLAN[street]
        self.burned.append(self.deck.pop(0))
        dealt = [self.deck.pop(0) for _ in range(count)]
        self.board.extend(dealt)
        self.note("board", street=street, cards=dealt)

    # ------------------------------------------------------- run it more

    def _streets_to_come(self) -> list[str]:
        if self.street not in STREETS:
            return []
        return list(STREETS[STREETS.index(self.street) + 1 :])

    def _cards_per_run(self) -> int:
        """Burn plus board cards one completion of the board would use."""
        return sum(1 + BOARD_PLAN[street] for street in self._streets_to_come())

    def runs_available(self, host_maximum: int) -> int:
        """How many times this hand could be run, deck permitting."""
        per_run = self._cards_per_run()
        if per_run == 0:
            return 1
        return max(1, min(int(host_maximum), len(self.deck) // per_run))

    def run_voters(self) -> list[int]:
        """Who gets a say: everyone still in the hand (section 6.6)."""
        return self.live()

    def choose_runs(self, seat_no: int, count: int) -> None:
        if self.street != RUN_CHOICE:
            raise IllegalAction("There is nothing to run twice right now.")
        if seat_no not in self.run_voters():
            raise IllegalAction("You are not in this hand.")
        if not 1 <= count <= self.max_runs:
            raise IllegalAction(f"Pick between 1 and {self.max_runs}.")
        self.run_choices[seat_no] = count
        self.note("run_choice", seat=seat_no, count=count)

    def everyone_has_chosen(self) -> bool:
        return all(seat in self.run_choices for seat in self.run_voters())

    def resolve_runs(self) -> None:
        """Settle on a number and deal the boards.

        Nothing chosen counts as one, and the lowest number anybody asked
        for is the one used — so running it more is only ever by agreement.
        """
        if self.street != RUN_CHOICE:
            return
        chosen = [self.run_choices.get(seat, 1) for seat in self.run_voters()]
        runs = max(1, min(chosen)) if chosen else 1
        self.config = replace(self.config, runs=runs)
        self.note("runs_settled", runs=runs, choices=dict(self.run_choices))
        self.street = self.pending_street
        self._deal_runs(runs)

    def _run_out(self) -> None:
        """Betting is over. Either ask how many runs, or deal them."""
        remaining = self._streets_to_come()
        someone_all_in = self._someone_is_all_in() and len(self.live()) > 1
        # Narrow the host's maximum by what the deck can actually deal.
        self.max_runs = self.runs_available(self.max_runs)

        if someone_all_in:
            # Nobody can bet again, so the hands go on their backs now, the
            # way they would on a real table. This is also what lets the
            # win percentages of section 6.6 be shown while the board runs
            # out: they are built only from cards that are already face up.
            for seat_no in self.live():
                self.seats[seat_no].revealed = True

        if remaining and someone_all_in and self.max_runs > 1:
            # Pause here: the players with a say choose, and the table layer
            # resolves it on its timer or as soon as they have all confirmed.
            self.pending_street = self.street
            self.street = RUN_CHOICE
            self.to_act = None
            self.note("run_choice_open", max_runs=self.max_runs)
            return

        self._deal_runs(max(1, self.config.runs))

    def _deal_runs(self, runs: int) -> None:
        remaining = self._streets_to_come()
        for _ in range(runs):
            completion: list[str] = []
            for street in remaining:
                self.burned.append(self.deck.pop(0))
                completion.extend(self.deck.pop(0) for _ in range(BOARD_PLAN[street]))
            self.run_boards.append(completion)

        self.street = SHOWDOWN
        self._finish()

    # ------------------------------------------------------------- finishing

    def boards(self) -> list[list[str]]:
        """The full board of each run."""
        if not self.run_boards:
            return [list(self.board)]
        return [self.board + completion for completion in self.run_boards]

    def showdown_order(self) -> list[int]:
        """Who shows first, then clockwise (section 6.8).

        The last player to bet or raise on the final betting round shows
        first. If nobody bet, the first player left of the button does.
        """
        live = self.live()
        if len(live) <= 1:
            return live
        start = self.last_aggressor
        if start is None or start not in live:
            start = self.order_from_button[0]
        sequence = [start] + [s for s in self.seat_order_from(start) if s in live]
        seen: list[int] = []
        for seat_no in sequence:
            if seat_no in live and seat_no not in seen:
                seen.append(seat_no)
        return seen

    def _finish(self) -> None:
        """Work out who gets what, pay it, and close the hand.

        Every chip that went in comes back out: uncalled bets are returned,
        the rest is layered into pots, and each pot is split evenly across
        the runs before being awarded. The tests assert that total chips
        never change.
        """
        live = self.live()
        order = self.order_from_button
        contributions = {s.seat: s.total for s in self.seats.values() if s.total > 0}

        contested, refunds = pot_math.return_uncalled(contributions)
        pots = pot_math.build_pots(contested, live)

        awards: dict[int, int] = dict(refunds)

        def credit(seat_no: int, amount: int) -> None:
            if amount:
                awards[seat_no] = awards.get(seat_no, 0) + amount

        uncontested = len(live) <= 1
        shown: dict[str, Any] = {}

        if uncontested:
            # Everyone folded. Nobody has to show, but the player left may
            # show one card or both if they feel like it (section 6.8).
            if live:
                credit(live[0], sum(pot.amount for pot in pots))
                self.show_choice = list(live)
            run_detail = [{"board": list(self.board), "pots": [], "uncontested": True}]
        else:
            boards = self.boards()
            runs = len(boards)
            run_detail = []

            for index, board in enumerate(boards):
                share_detail = []
                for pot_index, pot in enumerate(pots):
                    # Split each pot across the runs; the odd chips from that
                    # division go to the earlier runs so none are lost.
                    base, odd = divmod(pot.amount, runs)
                    amount = base + (1 if index < odd else 0)
                    ranked = {
                        seat: evaluate(self.seats[seat].hole + board)
                        for seat in pot.eligible
                    }
                    best = max(ranked.values())
                    winners = [s for s, rank in ranked.items() if rank == best]
                    payout = pot_math.share_out(
                        pot_math.Pot(amount, pot.eligible), winners, order
                    )
                    for seat_no, won in payout.items():
                        credit(seat_no, won)
                    share_detail.append(
                        {
                            "pot": pot_index,
                            "amount": amount,
                            "winners": sorted(winners),
                            "payout": {str(s): a for s, a in sorted(payout.items())},
                        }
                    )
                run_detail.append({"board": list(board), "pots": share_detail})

            # Who has to turn their hand over (section 6.8).
            #
            # If no further betting was possible — everyone left is all in,
            # which is also the only way a hand gets run more than once —
            # every hand goes face up automatically. Otherwise the players
            # show in order, and each one only has to show if their hand can
            # still beat or tie the best one already face up. A hand that
            # cannot win may be mucked, and a hand that wins or ties must be
            # shown to take the pot.
            with_chips = sum(1 for s in live if self.seats[s].stack > 0)
            all_in_showdown = with_chips <= 1

            best_shown: tuple | None = None
            for position, seat_no in enumerate(self.showdown_order()):
                seat = self.seats[seat_no]
                strength = evaluate(seat.hole + boards[0])
                must_show = (
                    all_in_showdown
                    or position == 0
                    or best_shown is None
                    or strength >= best_shown
                )
                if not must_show:
                    self.show_choice.append(seat_no)
                    continue
                seat.revealed = True
                best_shown = strength if best_shown is None else max(best_shown, strength)
                shown[str(seat_no)] = {
                    "hole": list(seat.hole),
                    "best": best_five(seat.hole + boards[0]),
                    "name": describe(seat.hole + boards[0]),
                }

        pot_size = self.pot_total
        for seat_no, amount in awards.items():
            self.seats[seat_no].stack += amount

        self.street = COMPLETE
        self.to_act = None
        self.result = {
            "awards": {str(s): a for s, a in sorted(awards.items())},
            "refunds": {str(s): a for s, a in sorted(refunds.items())},
            "runs": run_detail,
            "shown": shown,
            "uncontested": uncontested,
            "pot": pot_size,
        }
        self.note("hand_complete", awards=self.result["awards"])

    # -------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        return {
            "hand_no": self.hand_no,
            "config": self.config.to_dict(),
            "button": self.button,
            "seats": {str(k): v.to_dict() for k, v in self.seats.items()},
            "order": list(self.order),
            "deck": list(self.deck),
            "street": self.street,
            "board": list(self.board),
            "burned": list(self.burned),
            "run_boards": [list(b) for b in self.run_boards],
            "current_bet": self.current_bet,
            "min_raise": self.min_raise,
            "to_act": self.to_act,
            "last_aggressor": self.last_aggressor,
            "log": list(self.log),
            "result": self.result,
            "starting_stacks": {str(k): v for k, v in self.starting_stacks.items()},
            "run_choices": {str(k): v for k, v in self.run_choices.items()},
            "max_runs": self.max_runs,
            "pending_street": self.pending_street,
            "show_choice": list(self.show_choice),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hand:
        return cls(
            hand_no=data["hand_no"],
            config=Config.from_dict(data["config"]),
            button=data["button"],
            seats={int(k): Seat.from_dict(v) for k, v in data["seats"].items()},
            order=list(data["order"]),
            deck=list(data["deck"]),
            street=data["street"],
            board=list(data.get("board", [])),
            burned=list(data.get("burned", [])),
            run_boards=[list(b) for b in data.get("run_boards", [])],
            current_bet=data.get("current_bet", 0),
            min_raise=data.get("min_raise", 0),
            to_act=data.get("to_act"),
            last_aggressor=data.get("last_aggressor"),
            log=list(data.get("log", [])),
            result=data.get("result"),
            starting_stacks={
                int(k): v for k, v in data.get("starting_stacks", {}).items()
            },
            run_choices={int(k): v for k, v in data.get("run_choices", {}).items()},
            max_runs=data.get("max_runs", 1),
            pending_street=data.get("pending_street", RIVER),
            show_choice=[int(s) for s in data.get("show_choice", [])],
        )


# ------------------------------------------------------------- starting one


def start_hand(
    hand_no: int,
    button: int,
    stacks: dict[int, tuple[str, int]],
    config: Config,
    deck: list[str] | None = None,
    sb_seat: int | None = None,
    bb_seat: int | None = None,
    max_runs: int = 1,
) -> Hand:
    """Deal a new hand.

    `stacks` maps seat number to (player_id, chips). Seats are dealt in
    ascending seat order, which is clockwise around the table.

    `deck` is only ever passed by tests. In play the deck is shuffled here,
    once, from OS entropy.

    `sb_seat` and `bb_seat` let the table layer place the blinds itself,
    which it needs for the dead-button rule: the button can end up on an
    empty chair, and the small blind can be missing altogether. Left out,
    they are derived from the button the ordinary way.
    """
    order = sorted(stacks)
    if len(order) < 2:
        raise ValueError("a hand needs at least two players")

    hand = Hand(
        hand_no=hand_no,
        config=config,
        button=button,
        seats={
            seat: Seat(seat=seat, player_id=stacks[seat][0], stack=stacks[seat][1])
            for seat in order
        },
        order=order,
        deck=list(deck) if deck is not None else shuffled_deck(),
        min_raise=config.big_blind,
        starting_stacks={seat: stacks[seat][1] for seat in order},
    )

    hand.max_runs = max(1, int(max_runs))
    heads_up = len(order) == 2
    after_button = hand.seat_order_from(hand.button)

    if bb_seat is None:
        if heads_up:
            # The button is the small blind and acts first before the flop.
            sb_seat = hand.button if hand.button in order else after_button[0]
            bb_seat = next(s for s in after_button if s != sb_seat)
        else:
            sb_seat = after_button[0]
            bb_seat = after_button[1]

    if bb_seat not in hand.seats:
        raise ValueError("the big blind must be a seat in the hand")
    if sb_seat is not None and sb_seat not in hand.seats:
        sb_seat = None

    _post_antes(hand, bb_seat)

    if sb_seat is not None:
        hand._commit(
            hand.seats[sb_seat], min(config.small_blind, hand.seats[sb_seat].stack)
        )
        hand.note(
            "blind", seat=sb_seat, which="small", amount=hand.seats[sb_seat].committed
        )
    else:
        # Dead small blind: nobody posts it this hand.
        hand.note("dead_small_blind", seat=None)

    hand._commit(hand.seats[bb_seat], min(config.big_blind, hand.seats[bb_seat].stack))
    hand.note("blind", seat=bb_seat, which="big", amount=hand.seats[bb_seat].committed)

    hand.current_bet = config.big_blind
    hand.min_raise = config.big_blind
    last_posted = bb_seat

    straddle = config.straddle
    if straddle and straddle.get("seat") in hand.seats:
        seat_no = straddle["seat"]
        amount = max(int(straddle["amount"]), config.big_blind)
        straddler = hand.seats[seat_no]
        # The straddle is a total, not an extra. Normally the straddler is
        # under the gun and has nothing in yet; if they are also a blind,
        # the blind counts towards it rather than stacking on top.
        hand._commit(straddler, min(amount - straddler.committed, straddler.stack))
        hand.note("straddle", seat=seat_no, amount=straddler.committed)
        # A straddle is a raise: the next minimum raise is a full raise above
        # it, by the usual rule that the increment is the size of the last
        # raise.
        hand.min_raise = max(config.big_blind, amount - config.big_blind)
        hand.current_bet = max(hand.current_bet, amount)
        last_posted = seat_no

    _deal_hole_cards(hand)

    if heads_up and not straddle and sb_seat is not None:
        # Heads-up, the small blind (who is the button) acts first.
        hand.to_act = sb_seat if not hand.seats[sb_seat].all_in else None
    else:
        nxt = hand.seat_order_from(last_posted)
        hand.to_act = next(
            (s for s in nxt if not hand.seats[s].all_in and not hand.seats[s].folded),
            None,
        )

    # Blinds are posted, not acted: the big blind still has an option.
    for seat in hand.seats.values():
        seat.acted = False

    if hand.to_act is None or len(hand.actors()) <= 1:
        # Everyone was all in for their blind.
        hand._close_street()

    return hand


def _post_antes(hand: Hand, bb_seat: int) -> None:
    config = hand.config
    if config.ante <= 0 or config.ante_type == "none":
        return
    if config.ante_type == "big-blind":
        payers = [bb_seat]
        amount = config.ante * len(hand.order)
    else:
        payers = list(hand.order)
        amount = config.ante
    for seat_no in payers:
        seat = hand.seats[seat_no]
        paid = hand._commit(seat, min(amount, seat.stack))
        hand.note("ante", seat=seat_no, amount=paid)
    # Antes are dead money: they are in the pot but nobody is "called" by
    # them, so the street starts with no bet to match.
    for seat in hand.seats.values():
        seat.committed = 0


def _deal_hole_cards(hand: Hand) -> None:
    """One card at a time around the table, starting left of the button."""
    sequence = hand.order_from_button
    for _ in range(HOLE_CARDS):
        for seat_no in sequence:
            hand.seats[seat_no].hole.append(hand.deck.pop(0))
