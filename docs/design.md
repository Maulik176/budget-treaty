# Budget Treaty: design

A group of friends plans a trip in Discord. Each friend talks naturally to their own companion agent in their own
private channel ("I can do around 60-75k, I'm not veg"). The companion turns that into a brief the friend confirms,
and only the keeper (plain code) reads it. In the shared channel a guide agent researches the trip and registers
packages; the keeper checks each against everyone's limits and answers with one word: fits everyone, or doesn't.

## The claim

**Only your own companion and the keeper ever see your number. No other friend, and no shared AI, does.** The keeper
never writes it to a file or database of its own. What we don't claim: the host machine (Switch's database) and
Discord still store the messages, so the person who runs the server is trusted. Nothing here is zero-knowledge or
cryptographic, and the README says so.

## Rooms (Switch room membership is the wall)

| Room | Discord channel | Members | AI inside? |
|---|---|---|---|
| Trip | `#trip` | friends, `treaty-planner` as the guide (Claude Code, web search), `treaty-keeper` (code) | the guide only |
| One per friend | `#treaty-<name>`, private | that friend, their own companion `treaty-<name>` (Claude Code, no web), `treaty-keeper` | that friend's companion only |

`@treaty` is a per-room alias: in a private room it points at that friend's companion, in `#trip` at the guide.

The keeper is one process with one `scope=all` WebSocket: it hears every room it belongs to, and each event carries
its room id. The guide is a member of `#trip` only, so Switch never delivers it a private-room event. A companion is a
member of its own friend's room only.

## Messages between agents

- **Companion → keeper (private room):** after the friend says yes, the companion posts one line:
  `BRIEF | budget: 75000 | dates: 2026-12-10 to 2026-12-20 | avoid: none | food: non-veg | hotel: mid-range | flights: cheapest`.
  The keeper accepts it only from that room's companion. A budget range counts as its top. Food, hotel and flights
  are preferences, shared only as counts without names in the `group-preferences` document.
- **Guide → keeper (trip room):** `OPTION P1 | name | cost per person | 2026-12-12 to 2026-12-18 | tags: flight, mid-range`.
  Only the guide can add packages; each is checked at once; a checked id can't be re-priced in the same round.
- **Friends → keeper (trip room):** plain words. "count me in for P1", "not for me", and the organizer's "let's lock
  P1". A message counts as a vote only if it names exactly one known package.
- When everyone has set their limits, the keeper posts once: "All N friends have set their limits privately."

## State: Switch's room history is the only record

- Limits, rounds, verdicts, votes and the lock live in keeper memory only.
- On every start the keeper rebuilds them by replaying the rooms' history (`read_context`, paging with `before`) in
  timestamp order through the same `handle` used live, with output suppressed. Rate limits use message timestamps,
  so replay is deterministic.
- Duplicate events are dropped by message id.
- `trip-catalog` and `treaty-ledger` are room documents in `#trip` that only the keeper writes (only a document's
  creator can change it), so the planner reads exactly what the checker uses.

## Commands

Private channel (owner only): `budget 9000` · `dates 2026-12-12 2026-12-15` · `no flight` / `ok flight` · `show` · `clear`

Trip channel: `options` · `check B2` · `in B2` / `out B2` · `votes` · `status` · organizer only: `lock B2`, `new round`

## Probing defences

1. **Registered packages only.** `check 9200` is refused; only package ids are checked, and only the guide registers packages.
2. **One verdict for the group.** Never who failed or by how much.
3. **Readiness.** No verdict until every member has set a budget, and never with fewer than 3 members (with 2, every
   "doesn't fit" gives away the other person's number).
4. **Rounds.** Once a verdict has been given in a round, limit changes are staged and apply at `new round`, so "fits at
   14:00, fails at 14:02" can't reveal a change. `lock` always re-checks against everyone's latest limits; if the
   option no longer fits, the lock is refused and a new round starts.
5. **Rate limit.** 5 new checks per person per 10 minutes (15 for the guide, which checks on everyone's behalf). Repeating a check already answered this round is free. The
   limit message goes only to the person who hit it, in a thread.
6. **Public query ledger.** `treaty-ledger` records who checked what and the verdict, so probing is visible to friends.

## The wall tripwire

Before every verdict the keeper fetches each private room's participants from Switch. Anything other than that friend,
their own companion and the keeper suspends all checks and names the
intruder in `#trip`. Verdicts resume when it's removed.

## Tests (must pass before going live)

1. Probe: raw numbers refused; only catalog ids checked.
2. Two-friend leak: no verdicts with fewer than 3 members or before everyone set a budget.
3. Mid-round change: staged until `new round`; `lock` re-checks the latest limits.
4. Rebuild: replaying the same history gives the same state.
5. Duplicate event: handled once.
6. Tripwire: an intruder suspends checks; no verdict, no check counted.
7. Rate limit: 6th new check in 10 minutes refused; repeats free; allowed again later.
Plus: no private reply ever goes to `#trip`, and no limit value ever appears in `#trip`.
