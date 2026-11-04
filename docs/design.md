# Budget Treaty: design

A group of friends plans a trip in Discord. Each friend sets private limits (budget, dates, deal-breakers) in their
own private channel. A plain-code keeper checks catalog options against everyone's limits and answers in the shared
channel with one word: fits everyone, or doesn't. An AI planner helps the group choose, but it is never in a private
channel, so it never sees a limit.

## The claim

**No AI agent and no other friend ever sees your number.** The keeper never writes it to a file or database of its
own. What we don't claim: the host machine (Switch's database) and Discord still store the messages, so the person
who runs the server is trusted. Nothing here is zero-knowledge or cryptographic, and the README says so.

## Rooms (Switch room membership is the wall)

| Room | Discord channel | Members | AI inside? |
|---|---|---|---|
| Trip | `#trip` | friends, `treaty-planner` (Claude Code), `treaty-keeper` (code) | planner only |
| One per friend | `#treaty-<name>`, private | that friend, `treaty-keeper`, Switch's system accounts | none |

The keeper is one process with one `scope=all` WebSocket: it hears every room it belongs to, and each event carries
its room id. The planner is a member of `#trip` only, so Switch never delivers it a private-room event.

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

1. **Catalog options only.** `check 9200` is refused; only option ids are checked.
2. **One verdict for the group.** Never who failed or by how much.
3. **Readiness.** No verdict until every member has set a budget, and never with fewer than 3 members (with 2, every
   "doesn't fit" gives away the other person's number).
4. **Rounds.** Once a verdict has been given in a round, limit changes are staged and apply at `new round`, so "fits at
   14:00, fails at 14:02" can't reveal a change. `lock` always re-checks against everyone's latest limits; if the
   option no longer fits, the lock is refused and a new round starts.
5. **Rate limit.** 5 new checks per person per 10 minutes. Repeating a check already answered this round is free. The
   limit message goes only to the person who hit it, in a thread.
6. **Public query ledger.** `treaty-ledger` records who checked what and the verdict, so probing is visible to friends.

## The wall tripwire

Before every verdict the keeper fetches each private room's participants from Switch. Anything other than that friend,
the keeper and Switch's system accounts (`@switch-admin`, the Discord bridge) suspends all checks and names the
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
