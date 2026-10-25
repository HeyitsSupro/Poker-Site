"""Terminal test bots: fill a table without needing six friends.

They connect to a running server over the real WebSocket, sit down, buy in
and play, choosing at random from whatever the server says is legal. They do
not know the rules — the server tells them, which is the point: if a bot can
play a session without a complaint, the client-facing contract holds.

    # against a local server
    python3 tools/bots.py --players 5

    # against the deployed one, joining a table you already made
    python3 tools/bots.py --server https://your-service.onrender.com \
        --table <table id> --players 4 --hands 50

Stop them with Ctrl-C. They are harmless: play money, and they leave their
seats when they are done.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import urllib.request
import uuid

try:
    import websockets
except ImportError:  # pragma: no cover - a clearer message than a traceback
    sys.exit("pip install websockets")

NAMES = [
    "Bitsy", "Chip", "Dealer Dan", "Flopsy", "Gutshot", "Ivy", "Limper",
    "Nitty", "Rounder", "Shovey",
]


def post(url: str, body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


class Bot:
    def __init__(self, name: str, ws_url: str, origin: str, args, index: int) -> None:
        self.name = name
        self.ws_url = ws_url
        self.origin = origin
        self.args = args
        self.index = index
        self.rng = random.Random(name)
        self.player_id: str | None = None
        self.token: str | None = None
        self.hands_seen = 0
        self.last_hand_no = 0
        self.sitting_down = False
        # What the table looked like the last time this bot acted, so a
        # second copy of the same state does not make it act twice.
        self.acted_on: tuple | None = None

    async def run(self) -> None:
        async with websockets.connect(self.ws_url, origin=self.origin) as ws:
            await self.send(ws, {"t": "hello", "protocol": 1, "nick": self.name})
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.args.idle)
                except asyncio.TimeoutError:
                    # Nothing has happened for a while: the table has
                    # probably run out of players. Leave quietly.
                    print(f"[{self.name}] nothing happening, leaving")
                    return
                except websockets.ConnectionClosed:
                    return
                if await self.handle(ws, json.loads(raw)):
                    return

    async def send(self, ws, message: dict) -> None:
        await ws.send(json.dumps(message))

    async def act(self, ws, kind: str, **data) -> None:
        await self.send(
            ws,
            {"t": "action", "aid": str(uuid.uuid4()), "kind": kind, "data": data},
        )

    async def handle(self, ws, message: dict) -> bool:
        kind = message.get("t")

        if kind == "welcome":
            self.player_id = message.get("player_id")
            self.token = message.get("token")
            print(f"[{self.name}] joined")
            return False

        if kind == "error":
            # The server is the referee; a refusal is information, not a crash.
            if message.get("code") == "rejected":
                self.sitting_down = False
            print(f"[{self.name}] refused: {message.get('message')}")
            return False

        if kind != "state":
            return False

        state = message["state"]
        me = state.get("you") or {}

        if me.get("seat") is None and not state.get("ended"):
            await self.take_a_seat(ws, state, me)
            return False

        self.sitting_down = False
        hand = state.get("hand")
        if hand and hand.get("hand_no", 0) > self.last_hand_no:
            self.last_hand_no = hand["hand_no"]
            self.hands_seen += 1
            if self.args.hands and self.hands_seen > self.args.hands:
                print(f"[{self.name}] done after {self.hands_seen - 1} hands")
                await self.act(ws, "stand")
                return True

        legal = me.get("legal") or {}
        if not legal.get("actions"):
            return False

        # Only act once per turn: several state broadcasts can arrive while
        # it is still this bot's go.
        signature = (
            hand.get("hand_no") if hand else None,
            hand.get("street") if hand else None,
            hand.get("to_act") if hand else None,
            legal.get("current_bet"),
            legal.get("to_call"),
        )
        if signature == self.acted_on:
            return False
        self.acted_on = signature

        await self.choose(ws, legal)
        return False

    async def take_a_seat(self, ws, state: dict, me: dict) -> None:
        if self.sitting_down:
            return
        taken = {seat["seat"] for seat in state.get("seats", [])}
        seat_count = state["settings"]["seat_count"]
        # Each bot aims at its own chair first, so they do not all grab seat
        # zero and get turned away.
        preferred = [(self.index + offset) % seat_count for offset in range(seat_count)]
        free = [n for n in preferred if n not in taken]
        if not free:
            return
        self.sitting_down = True
        if me.get("held"):
            await self.act(ws, "sit", seat=free[0], resume=True)
        else:
            await self.act(ws, "sit", seat=free[0], buy_in=state["settings"]["buy_in"])

    async def choose(self, ws, legal: dict) -> None:
        """Pick something legal, with a bias towards actually playing."""
        actions = legal["actions"]
        weighted = []
        for action in actions:
            weight = {"fold": 1, "check": 4, "call": 3, "bet": 2, "raise": 2}.get(
                action, 1
            )
            weighted += [action] * weight
        action = self.rng.choice(weighted)

        amount = None
        if action in ("bet", "raise"):
            low, high = legal["min_raise_to"], legal["max_raise_to"]
            if low > high:
                action = "call" if "call" in actions else "check"
            else:
                # Mostly small raises, occasionally everything.
                amount = (
                    high
                    if self.rng.random() < 0.08
                    else self.rng.randint(low, min(high, max(low, low * 3)))
                )

        await asyncio.sleep(self.rng.uniform(*self.args.think))
        await self.act(ws, "act", action=action, amount=amount)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--table", default=None, help="join an existing table")
    parser.add_argument("--players", type=int, default=4)
    parser.add_argument(
        "--hands", type=int, default=0, help="stop after this many (0 = forever)"
    )
    parser.add_argument(
        "--origin",
        default=None,
        help="Origin header; must match ALLOWED_ORIGIN on the server",
    )
    parser.add_argument(
        "--idle",
        type=float,
        default=30.0,
        help="leave if nothing happens for this many seconds",
    )
    parser.add_argument(
        "--think",
        type=float,
        nargs=2,
        default=(0.2, 0.9),
        metavar=("MIN", "MAX"),
        help="seconds to pause before acting",
    )
    args = parser.parse_args()

    server = args.server.rstrip("/")
    table_id = args.table or post(f"{server}/api/tables", {"name": "Bot table"})[
        "table_id"
    ]
    ws_url = server.replace("http", "ws", 1) + f"/ws?table={table_id}"

    print(f"table: {server.replace('://', '://')}  id: {table_id}")
    print(f"open:  <your page>/?t={table_id}")

    bots = [
        Bot(
            NAMES[i % len(NAMES)] + ("" if i < len(NAMES) else str(i)),
            ws_url,
            args.origin or server,
            args,
            i,
        )
        for i in range(args.players)
    ]
    await asyncio.gather(*(bot.run() for bot in bots), return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped")
