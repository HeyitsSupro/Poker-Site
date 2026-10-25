# Felt — a private poker table for home games

Play-money Texas Hold'em for a group of friends. Free to run: a static page
on GitHub Pages, a Python server on Render's free tier, and a Neon free
Postgres.

`Felt` is a placeholder name. The site is named in exactly one place —
`SITE_NAME` in `web/public/config.js` (and `docs/config.js` once built).
Change it there and it changes everywhere, including the home-screen app
name.

---

## What works

Phases 0 to 5 of the build instructions are done. In short: you can run a
full home game.

**The game.** No-limit, pot-limit and fixed-limit Texas Hold'em, 2–10 seats,
heads-up included. Blinds, antes (every player, or big-blind ante) and
straddles. The two house rules — a preflop cap and a maximum straddle — are
enforced by the server and shown in the UI, and limits compose, so the
allowed range is always the tightest of the ones that are on. Side pots,
odd chips, dead button, short all-ins not reopening the betting: all the
usual conventions, all tested.

**Run it twice or more.** When someone is all in with board to come, the
players still in the hand pick a number and the lowest one asked for is
used. It starts when everyone has confirmed or the timer ends. Each pot is
split across the runs, and win percentages are computed on the server from
cards that are already face up.

**Rabbit hunting.** After a hand that ended early, the cards that never came
sit face down in their board positions. Anyone can turn one over and it
turns over for everybody. A card nobody has flipped is not in the network
traffic at all.

**Showdown.** Proper order, and a player who cannot win may muck rather than
show. A hand that wins or ties is turned over by the server. Auto-muck is a
per-player preference.

**Clocks.** An action clock with a time bank per player, auto check-or-fold
on timeout, away players folded automatically, and a pause between hands.
The host can turn any of it off.

**Money.** A ledger that always balances: buy-ins, rebuys, held stacks,
cash-outs, reversed cash-outs and host stack edits. Standing up holds your
chips rather than losing them, and resuming them is not a new buy-in. Log
and ledger are visible to everyone and downloadable, and there is a session
summary in the owner's own tracker format.

**Records.** A live log, and a hand history you can step through hand by
hand. What a replay shows you is what you could have seen at the time: your
own cards, hands turned face up at showdown, the board and the betting. A
hand somebody mucked stays mucked.

**The rest.** Waitlist and seat requests, host and co-hosts with a host
code, buy-in approvals, setting presets, rathole time, private notes about
other players, badges, chat, emoji reactions, spectators, four-colour deck,
stacks in chips or big blinds, light and dark themes, felt and card-back
choices, keyboard shortcuts, a your-turn alert, and add-to-home-screen.

**Not built, by design.** Other game variants (Omaha, short deck), bomb
pots, double boards, mixed games, blind schedules and tournaments. These are
phase 6 in the build instructions. The engine was written with pluggable
hole-card counts, board structure and betting structures so they can be
added without unpicking it.

---

## Layout

```
server/        Python backend (this folder is Render's "root directory")
  app/
    main.py        FastAPI: health, table creation, the WebSocket
    table.py       the table: seats, hand lifecycle, what each viewer sees
    host.py        host controls and settings
    ledger.py      the money: buy-ins, held stacks, cash-outs, net
    identity.py    players, tokens, rejoin links
    clock.py       deadlines (action clock, run-it-more, showdown)
    ticker.py      wakes a table when a deadline falls due
    store.py       save-after-every-action persistence
    db.py          asyncpg pool that survives Neon's scale-to-zero
    engine/        the poker itself: pure, no network, no database
      holdem.py      one hand, as a state machine
      limits.py      composable betting limits
      pots.py        side pots, odd chips, payouts
      evaluator.py   hand ranking
      equity.py      win and tie percentages
      cards.py       cards and the one shuffle per hand
web/           frontend source (Svelte 5 + Vite)
docs/          the built frontend; GitHub Pages serves this folder
tests/         backend tests (pytest)
web/test/      frontend tests (node --test)
```

---

## Run it on your own machine

You need Python 3.11+ and Node 20+.

**1. A database.** Any Postgres will do locally; the Neon connection string
from the deployment steps below works from your laptop too.

```bash
createdb poker
```

**2. The server.**

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL='postgresql://localhost/poker?sslmode=disable'
export ALLOWED_ORIGIN='http://127.0.0.1:5173'
uvicorn app.main:app --reload --port 8000
```

<http://127.0.0.1:8000/healthz> should answer `{"ok": true, "ready": true, …}`.
`ready` is false while the database is unreachable — that is the signal the
wake-up screen watches.

**3. The frontend.**

```bash
cd web
npm install
npm run dev          # http://127.0.0.1:5173
```

`web/public/config.js` points at the deployed Render backend, so the dev
server talks to the real one out of the box. To work against the server on
your own machine instead, set `BACKEND_URL` to `http://localhost:8000` while
you do — and put it back before building and pushing, or the published site
will work on your laptop and nowhere else. `npm run build` warns you if you
forget.

Open it, press **Start a table**, pick a name, sit down. Open the same table
link in a second browser *profile* — not just a second tab, which shares the
saved token and is treated as the same player returning.

**4. Build the static site** the way GitHub Pages will serve it:

```bash
cd web && npm run build      # writes ../docs
```

---

## Tests

```bash
createdb poker_test
TEST_DATABASE_URL='postgresql://localhost/poker_test?sslmode=disable' python3 -m pytest
cd web && npm test
```

227 backend tests and 9 frontend tests. Tests needing a database skip when
`TEST_DATABASE_URL` is unset, so plain `pytest` still runs the rest.

What they cover, beyond the obvious: hand ranking cross-checked against
PokerKit over random boards; pot-limit test vectors; the preflop cap and max
straddle at their defaults and when set; side pots, odd chips and
run-it-more payouts; fuzz runs asserting chips are conserved and no stack
goes negative; save and restore at random points mid-hand producing the same
outcome as an uninterrupted run; and a set of protocol tests that read the
actual outgoing messages and prove no hole card, no undealt card and no deck
ever reaches someone who should not have it.

---

## Deploying it (one time, about 20 minutes)

Three free services. Do them in this order; step 3 needs the address from
step 2.

### 1. The database (Neon)

1. Sign up at <https://neon.com> and create a project. Any region near your
   players; the defaults are fine.
2. On the project dashboard, find **Connection string** and copy it. It
   looks like
   `postgresql://user:password@ep-something-pooler.region.aws.neon.tech/neondb?sslmode=require`.
3. Keep it somewhere safe for the next step. It is a password — it never
   goes in the repo.

The server creates its own tables on first boot.

> Do **not** use Render's own free Postgres. It is deleted 30 days after it
> is created.

### 2. The server (Render)

1. Push this repo to GitHub.
2. Sign up at <https://render.com>, then **New > Web Service**.
3. Connect GitHub and pick this repository.
4. Fill in the form:
   - **Name**: anything, e.g. `poker-server`. You will get
     `https://poker-server-xxxx.onrender.com`.
   - **Root Directory**: `server`
   - **Language**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: **Free**
5. Open **Advanced** and set:
   - **Health Check Path**: `/healthz`
   - **Auto-Deploy**: **No**. Every deploy restarts the server, and you do
     not want that mid-hand. Deploy by hand, between hands.
6. Add environment variables:
   - `DATABASE_URL` — the Neon connection string from step 1.
   - `ALLOWED_ORIGIN` — your GitHub Pages address, no trailing slash, e.g.
     `https://yourname.github.io`. You will know it after step 3; put a
     placeholder in now and correct it then.
   - `PYTHON_VERSION` — `3.13.9`. (Render's current default is 3.14, which
     not every dependency has wheels for yet. `server/.python-version` pins
     the same thing if you would rather not set the variable.)
7. **Create Web Service** and wait for the first deploy.
8. Open `https://<your-service>.onrender.com/healthz`. The first request may
   take a minute while the service starts. You want
   `{"ok": true, "ready": true, …}`. If `ready` is false the `DATABASE_URL`
   is wrong, and the Render **Logs** tab will say so.

### 3. The page (GitHub Pages)

1. Check `web/public/config.js`. It already holds this deployment's values:

   ```js
   SITE_NAME: 'Poker Site',
   BACKEND_URL: 'https://poker-site-sdou.onrender.com',
   ```

   > **This is the step that catches people out**, so it is worth knowing
   > why. If `BACKEND_URL` is ever a local address, it works on the machine
   > running the server and nowhere else — on a phone, "localhost" means the
   > phone. The whole site then looks fine to you and dead to everybody
   > else. It must also be `https://`, not `http://`, because an HTTPS page
   > is not allowed to call an insecure one. `npm run build` warns if the
   > address is local, and the page itself names the problem on screen.
2. Rebuild and commit:
   ```bash
   cd web && npm install && npm run build
   cd .. && git add docs web && git commit -m "Point at the backend" && git push
   ```
3. On GitHub: **Settings > Pages**. Set **Source** to *Deploy from a branch*,
   **Branch** to `main`, folder **`/docs`**. Save.
4. Wait a minute, then open the address GitHub shows you.
5. Back in Render, set `ALLOWED_ORIGIN` to exactly that address (scheme and
   host, no path, no trailing slash). Save; Render restarts.

> `docs/config.js` is a plain file in the built output. To change the backend
> address or the site name later you can edit that file and push — no rebuild.

### 4. Smoke test

1. Leave it 20 minutes so the server sleeps.
2. Open the Pages address cold: "waking up the table" for up to a minute,
   then the lobby.
3. Start a table, sit down, copy the link, open it on your phone, sit down as
   someone else, play a hand.
4. In Render, **Manual Deploy > Restart service**. Both screens should
   reconnect by themselves onto the same hand.

---

## Decisions taken (section 12 of the build instructions)

- **Engine: our own, not PokerKit.** PokerKit was tried against the three
  things section 5 asks for. It deals from our deck happily, but its
  maximum-raise is a read-only derived property, so a house-rule cap cannot
  be injected without reaching inside it, and its state round-trips only
  through `pickle`, which breaks across library upgrades and redeploys —
  exactly what section 4.2 forbids. It is used in the tests instead, as a
  second opinion on hand ranking over random boards.
- **Frontend: Svelte 5 + Vite.** Compiles away rather than shipping a
  runtime; the whole app is about 38 kB gzipped, which matters on mobile
  data. No state-management library: the connection object is the store.
- **Protocol: JSON over one WebSocket, full per-viewer snapshots rather than
  diffs.** A table state is a few kilobytes, so resending removes a whole
  class of desync bugs. Each snapshot is built for one viewer by `view_for`,
  so hidden information is filtered where it is serialised, not where it is
  rendered — one place to get wrong, and tests watch that place.
- **Database: one JSONB state row per table, plus append-only `events` and
  `hands`.** The game is a single consistent object; splitting it across
  relational tables would buy queries nobody runs and cost atomicity on
  every action. `SELECT … FOR UPDATE` on the state row serialises actions on
  a table, which is exactly the invariant poker needs.
- **Saving: one transaction per action**, holding the new state, the log
  events and the action id together. The client is told only after commit.
- **Duplicate actions: a unique `(table_id, action_id)` row.** A replay after
  a reconnect is answered from the committed state. This is also what makes
  retrying a database call safe when Neon drops a connection mid-transaction.
- **Timers are actions.** A deadline lives in the saved state; a small
  per-table task wakes at it and submits a `tick` whose action id is derived
  from the deadline, so the same deadline can never fire twice. Restarting
  the server rebuilds every pending timer from the database.
- **Countdowns are sent as seconds remaining**, not as absolute times, so a
  phone with a wrong clock still shows the right number. The client counts
  down locally between snapshots and re-anchors on each one.
- **Win percentages** are computed once per change — when the board or the
  set of face-up hands moves — rather than once per viewer, and cached in
  the state. Exact enumeration when the remaining combinations are few,
  sampling otherwise. Only cards already face up to everyone go in.
- **Identity**: a 256-bit server-generated token, stored hashed, kept per
  table in the browser; plus a personal rejoin link and a host-approved
  claim for a new device. A nickname proves nothing. One active device per
  player: a second connection takes over and the first is told.
- **Hand-history redaction**: your own cards, plus anything shown at
  showdown. Mucked hands stay mucked — being able to read back what everyone
  folded all night is a bigger edge than anything else on the site.
- **Private notes live on the server**, so they follow you to a new device,
  and are sent only to their author and never written to the table log.
- **Odd chips** go to the first seat clockwise from the button among the
  winners; **the button** follows the standard rule that the big blind never
  moves backwards, with a dead button and a dead small blind where needed;
  **a short all-in** does not reopen betting for players who have already
  acted. All of these have tests.

---

## About the free tiers

Checked 24 October 2026. Re-check before relying on any of it.

**Render free web services** spin down after 15 minutes without inbound
traffic and take about a minute to come back; WebSocket messages on an open
connection count as traffic, which is what the heartbeat is for. No
persistent disks, so nothing may live on the server's filesystem. Each
workspace gets 750 instance hours a month — enough for a sleeping service
and plenty of game nights, but do not add an external pinger to keep it
awake: that burns the month's hours and gains nothing the wake screen does
not already handle.

**Neon free** gives 1 GB of storage and 100 compute-hours per project per
month, with 5 GB of transfer; compute suspends after 5 idle minutes and
resumes on the next query, which is the case `db.py` retries around. Hand
histories are stored as one compact document per hand to stay well inside
the storage limit.

**GitHub Pages** serves static files only, which is why the page has to load
and explain itself while the backend is still asleep.

Sources: [Render free tier](https://render.com/docs/free),
[Render Python versions](https://render.com/docs/python-version),
[Neon free plan limits](https://neon.com/faqs/free-plan-limits-and-quotas).

---

## Things worth knowing

- **Deploys restart the server.** Auto-deploy is off for that reason. Deploy
  between hands.
- **The table link is the only thing gating entry.** It carries a long random
  id and the page asks not to be indexed. Anyone with the link can sit down.
  The host can add a password.
- **Ad blockers and privacy shields can block the table.** The page and the
  server are on different domains, which is exactly the shape of request a
  blocker is built to stop; Brave Shields in particular will do it on its
  default setting. If the table works in one browser and not another, that
  is the cause — turn the shield off for the site. The page says so when it
  cannot get through.
- **Chips are bookkeeping.** No money, no payments, no cashier. Settling up
  happens away from the site.
- **Nothing here is PokerNow's.** The layout follows the owner's screenshot,
  but the code, card faces, card backs, chips, icons, colours, sounds and
  wording are all original.
