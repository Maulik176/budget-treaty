# Budget Treaty

Plan a group trip with friends in Discord without anyone having to say their budget out loud.

Each friend sets private limits (budget, dates, deal-breakers) in their own private channel. A plain-code keeper
checks trip options against everyone's limits and answers in the shared channel with one word: **fits everyone**, or
**doesn't fit everyone**. An AI planner helps the group choose, but it is never in a private channel.

Built for the Flip the Switch hackathon (Track 02, Personal or Creative Workflow) on
[Switch](https://github.com/sandbox-quantum/switch). Work in progress: this README grows with the project.

## What we claim, and what we don't

- **No AI agent and no other friend ever sees your number.** The planner isn't a member of any private room, so Switch
  never delivers it a private message. Before every answer the keeper asks Switch who is in each private room; if
  anything other than that friend and the keeper is there, it stops answering and names the intruder.
- **The keeper never writes your number down.** It keeps limits in memory and rebuilds them from Switch's own room
  history when it restarts.
- **Not claimed:** the machine running Switch stores the messages in its database, and Discord stores them too, so
  whoever runs the server is trusted. Nothing here is zero-knowledge or cryptographic.

## Probing defences

Only catalog options can be checked (never a typed amount). One verdict for the whole group. No answers with fewer
than 3 friends or before everyone has set a budget. Limit changes wait for the next round once a verdict exists, and
`lock` re-checks everyone's latest limits. 5 new checks per person per 10 minutes; repeats are free. A ledger shows
who checked what. Details: [docs/design.md](docs/design.md).

## Commands

Private channel: `budget 9000` · `dates 2026-12-12 2026-12-15` · `no flight` / `ok flight` · `show` · `clear`

Trip channel: `options` · `check B2` · `in B2` / `out B2` · `votes` · `status` · organizer: `lock B2`, `new round`

## Run the tests

```bash
uv sync
uv run pytest -q
```

## Run the keeper

Needs Switch Console 0.40.1 with its local server running and a Discord server connected.

```bash
uv run treaty-keeper register
uv run treaty-keeper whois --room "<Switch room name>"
uv run treaty-keeper run --config trips/example/trip.json
```

`register` reads a registration key from `~/.config/budget-treaty/registration_key` and stores the keeper's own key
in `~/.config/budget-treaty/agent.json` (mode 600). Copy `trips/example/` and fill in your room names and your
friends' Discord usernames. Full setup steps will be documented here.
