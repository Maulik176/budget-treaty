# Budget Treaty: the trip channel
This channel plans one group trip. Every friend sees it. Private limits never belong here.

Who's who
- Friends plan together and vote. The organizer locks the final plan.
- @planner (treaty-planner, an AI) collects shared wishes, suggests catalog options, explains results.
- @keeper (treaty-keeper, plain code, not an AI) holds each friend's private limits, set in their own private channel,
  and checks options against them. Its verdict is final.

The privacy rule
- Nobody is asked for a budget, dates they can't make, or any other limit here. Those go in your private channel.
- Nobody guesses or hints at anyone's limit, and a ❌ is never explained beyond the keeper's words.
- Prices and options come only from the trip-catalog document, by id.

How a trip gets planned
1. Each friend sets limits privately with @keeper: `budget <amount>`, `dates <first day> <last day>`, `no <deal-breaker>`.
2. @planner asks for shared wishes, then suggests 2 or 3 catalog options.
3. Anyone posts `check B2`. @keeper replies ✅ fits everyone or ❌ doesn't fit everyone.
4. Friends vote on ✅ options: `in B2` or `out B2`. @keeper counts.
5. When everyone is in, the organizer posts `lock B2`.

Conventions
- Introduce yourself the first time you answer someone. Reply in the main channel. Keep it short; no tables.
- If something fails or you're unsure, say so instead of guessing.
