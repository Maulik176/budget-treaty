"""The treaty: private limits, rounds, checks, votes and the lock. No Switch code and no AI here.

Every message from every room goes through `handle`, in order. On restart the keeper rebuilds the same state by
replaying Switch's own room history through `handle` with output discarded, so it never writes a limit to disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any

TRIP = "trip"
PRIVATE = "private"
MIN_GROUP = 3
NEW_CHECKS_PER_WINDOW = 5
WINDOW_MS = 10 * 60 * 1000
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

Outgoing = tuple[str, str | None, str]  # (TRIP or PRIVATE, private room owner or None, text)


@dataclass(frozen=True)
class Limits:
    budget: int | None = None
    window: tuple[date, date] | None = None  # the dates this friend can travel
    excluded: frozenset[str] = frozenset()  # deal-breaker tags, e.g. "flight"


@dataclass
class Treaty:
    catalog: dict[str, Any]
    members: dict[str, str]  # member key (messaging-app username) -> display name; each has a private room
    organizer: str
    min_group: int = MIN_GROUP  # lower only for a solo rehearsal; the leak argument needs 3 or more
    companions: dict[str, str] = field(default_factory=dict)  # member -> their own companion agent's name
    guide: str = ""  # the shared guide agent's name: the only sender whose OPTION messages add packages
    guide_checks_per_window: int = 15  # the guide checks on everyone's behalf, so it gets more
    preferences: dict[str, dict[str, str]] = field(default_factory=dict)  # member -> food/hotel/flights (not money)
    announced_ready: bool = False
    applied: dict[str, Limits] = field(default_factory=dict)  # what verdicts use this round
    staged: dict[str, Limits] = field(default_factory=dict)  # everyone's latest limits
    round: int = 1
    verdicts: dict[str, bool] = field(default_factory=dict)  # option id -> fits everyone, this round
    new_checks: dict[str, list[int]] = field(default_factory=dict)  # sender -> timestamps of new checks
    votes: dict[str, dict[str, str]] = field(default_factory=dict)  # option id -> member -> "in" | "out"
    locked: str | None = None
    ledger: list[str] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.options = {o["id"].upper(): o for o in self.catalog.get("options", [])}
        self.currency = self.catalog.get("currency", "")
        self.catalog_version = 0  # bumped when the guide adds a package, so the adapter can refresh trip-catalog

    # ── Entry point ────────────────────────────────────────────────────────────────────────────────────────────

    def handle(self, event: dict[str, Any]) -> list[Outgoing]:
        """event = {"id", "room": TRIP | PRIVATE, "owner" (private only), "sender", "body", "ts" (ms)}.

        For `check`, the adapter adds "wall": (ok, detail) from a live Switch participant audit.
        """
        if event["id"] in self.seen:
            return []
        self.seen.add(event["id"])
        if event["room"] == PRIVATE:
            return self._private(event)
        if event["room"] == TRIP:
            return self._trip(event)
        return []

    def replay(self, events: list[dict[str, Any]]) -> None:
        """Rebuild state from room history (already merged in timestamp order). Nothing is posted."""
        for event in events:
            self.handle({**event, "wall": (True, "")})

    # ── Private channels ───────────────────────────────────────────────────────────────────────────────────────

    def _private(self, event: dict[str, Any]) -> list[Outgoing]:
        owner, sender = event["owner"], event["sender"]
        if owner not in self.members:
            return []
        if sender == self.companions.get(owner) and owner in self.companions:
            return self._brief(owner, event["body"])  # the friend's own companion, writing what the friend agreed
        if sender != owner:
            return []  # only the channel's own friend (or their companion) can set its limits
        words = _words(event["body"])
        if not words:
            return []
        verb, args = words[0], words[1:]
        current = self.staged.get(owner, Limits())
        if verb == "show":
            return [self._say_private(owner, self._describe(owner))]
        if verb == "clear":
            return [self._say_private(owner, self._store(owner, Limits(), "Cleared your limits."))]
        if verb == "budget":
            amount = _parse_amount(" ".join(args))
            if amount is None:
                return [self._say_private(owner, "Use: budget 9000 (a whole amount per person, for the whole trip).")]
            return [self._say_private(owner, self._store(owner, replace(current, budget=amount),
                                                         f"Saved: budget {self._money(amount)}."))] + self._maybe_announce_ready()
        if verb == "dates":
            window = _parse_window(" ".join(args))
            if window is None:
                return [self._say_private(owner, "Use: dates 2026-12-12 2026-12-15 (first and last day you can travel).")]
            return [self._say_private(owner, self._store(owner, replace(current, window=window),
                                                         f"Saved: you can travel {window[0]:%d %b} to {window[1]:%d %b}."))]
        if verb in ("no", "ok") and len(args) == 1:
            tag = args[0]
            excluded = current.excluded | {tag} if verb == "no" else current.excluded - {tag}
            return [self._say_private(owner, self._store(owner, replace(current, excluded=frozenset(excluded)),
                                                         f"Saved: {'no' if verb == 'no' else 'fine with'} {tag}."))]
        return []

    def _brief(self, owner: str, body: str) -> list[Outgoing]:
        """`BRIEF | budget: 12000 | dates: 2026-12-12 to 2026-12-18 | avoid: flight | food: non-veg | hotel: …`"""
        brief = _parse_brief(body)
        if brief is None:
            return []  # the companion chatting with its friend
        current = self.staged.get(owner, Limits())
        limits = replace(current,
                         budget=brief.get("budget", current.budget),
                         window=brief.get("window", current.window),
                         excluded=brief.get("avoid", current.excluded))
        self.preferences[owner] = {**self.preferences.get(owner, {}), **brief.get("prefs", {})}
        out = [self._say_private(owner, self._store(owner, limits, "Saved your brief."))]
        return out + self._maybe_announce_ready()

    def _maybe_announce_ready(self) -> list[Outgoing]:
        if self.announced_ready or self._not_ready():
            return []
        self.announced_ready = True
        return [self._say(f"✅ All {len(self.members)} friends have set their limits privately. "
                          "I can check trip packages now; nobody's numbers will be shown.")]

    def preferences_text(self) -> str:
        """What the group wants, as counts with no names (money and dates are never included)."""
        lines = []
        for key in ("food", "hotel", "flights"):
            values = [p[key] for p in self.preferences.values() if p.get(key)]
            if values:
                counts = {v: values.count(v) for v in dict.fromkeys(values)}
                lines.append(f"{key}: " + ", ".join(f"{v} ×{n}" for v, n in counts.items()))
        ready = sum(1 for m in self.members if self.applied.get(m, Limits()).budget)
        lines.append(f"{ready} of {len(self.members)} friends have set their limits privately.")
        return "\n".join(lines)

    def _store(self, owner: str, limits: Limits, said: str) -> str:
        self.staged[owner] = limits
        who = "you, your companion and the keeper (plain code)" if owner in self.companions else "the keeper (plain code)"
        if not self.verdicts:  # no verdict yet this round: nothing to compare against, so apply now
            self.applied[owner] = limits
            return f"🔒 {said} Only {who} can see this."
        return f"🔒 {said} It takes effect next round (checks already ran this round). Only {who} can see this."

    def _describe(self, owner: str) -> str:
        latest = self.staged.get(owner, Limits())
        parts = [f"budget {self._money(latest.budget)}" if latest.budget else "no budget set"]
        if latest.window:
            parts.append(f"dates {latest.window[0]:%d %b} to {latest.window[1]:%d %b}")
        if latest.excluded:
            parts.append("no " + ", no ".join(sorted(latest.excluded)))
        pending = " (some changes apply next round)" if latest != self.applied.get(owner, Limits()) else ""
        return "Your limits: " + "; ".join(parts) + pending

    # ── The trip channel ───────────────────────────────────────────────────────────────────────────────────────

    def _trip(self, event: dict[str, Any]) -> list[Outgoing]:
        words = _words(event["body"])
        if not words:
            return []
        verb, args, sender = words[0], words[1:], event["sender"]
        if self.guide and sender == self.guide and verb == "option":
            return self._add_option(event)
        natural = self._natural(sender, event["body"]) if verb not in ("check", "in", "out", "lock", "new") else None
        if natural:
            verb, args = natural
        if verb == "options":
            return [self._say(self._list_options())]
        if verb == "status":
            return [self._say(self._status())]
        if verb == "votes":
            return [self._say(self._tally())]
        if verb not in ("check", "in", "out", "lock", "new"):
            return []  # friends and the planner talking
        if verb == "new" and args == ["round"]:  # the organizer can also reopen a locked trip this way
            return self._new_round(sender)
        if self.locked:
            return [self._say(f"The trip is locked: {self._label(self.locked)}. "
                              f"{self._name(self.organizer)} can reopen it with `new round`.")]
        if not args:
            return [self._say(f"Use: {verb} <option id>, e.g. {verb} {next(iter(self.options))}.")]
        option = args[0].upper()
        if option not in self.options:
            return [self._say("Only catalog options can be checked, by id: " + ", ".join(self.options) + ".")]
        if verb == "check":
            return self._check(event, option)
        if verb in ("in", "out"):
            return self._vote(sender, option, verb)
        return self._lock(sender, option)

    def _natural(self, sender: str, body: str) -> tuple[str, list[str]] | None:
        """Votes and the lock in plain words ("count me in for P1", "let's lock P1"). Exactly one known option id."""
        if sender not in self.members:
            return None
        ids = {m.upper() for m in re.findall(r"\b([A-Za-z]\d{1,2})\b", body)} & set(self.options)
        if len(ids) != 1:
            return None
        option, text = ids.pop(), body.lower()
        if sender == self.organizer and re.search(r"\block\b", text):
            return "lock", [option]
        if re.search(r"\b(i'?m out|count me out|not for me|pass on|no to|can'?t do|nope)\b|👎", text):
            return "out", [option]
        if re.search(r"\b(i'?m in|count me in|in for|works for me|i vote|vote for|let'?s do|i'?m down|sounds good|"
                     r"yes)\b|\+1|👍", text):
            return "in", [option]
        return None

    def _add_option(self, event: dict[str, Any]) -> list[Outgoing]:
        """The guide registers a package: `OPTION P1 | name | cost per person | 2026-12-12 to 2026-12-18 | tags: a, b`."""
        option = _parse_option(event["body"])
        if option is None:
            return [self._say("I couldn't read that package. Format: OPTION P1 | name | cost per person | "
                              "2026-12-12 to 2026-12-18 | tags: flight, mid-range")]
        if self.locked:
            return [self._say(f"The trip is locked: {self._label(self.locked)}.")]
        oid, existing = option["id"], self.options.get(option["id"])
        if existing and oid in self.verdicts and _terms(existing) != _terms(option):
            return [self._say(f"{oid} was already checked this round, so it can't be changed. Use a new id.")]
        self.options[oid] = option
        self.catalog_version += 1
        return self._check(event, oid)

    def _check(self, event: dict[str, Any], option: str) -> list[Outgoing]:
        ok, detail = event.get("wall", (True, ""))
        if not ok:
            return [self._say(f"⚠️ Wall breach: {detail}. Checks are suspended until it's fixed.")]
        not_ready = self._not_ready()
        if not_ready:
            return [self._say(not_ready)]
        if option in self.verdicts:  # already answered this round: free, and teaches nothing new
            return [self._say(self._verdict_text(option))]
        sender, now = event["sender"], int(event["ts"])
        recent = [t for t in self.new_checks.get(sender, []) if now - t < WINDOW_MS]
        allowed = self.guide_checks_per_window if self.guide and sender == self.guide else NEW_CHECKS_PER_WINDOW
        if len(recent) >= allowed:
            again = _clock(min(recent) + WINDOW_MS)
            return [self._say(f"{self._name(sender)}, you've used {allowed} new checks in 10 minutes. "
                              f"Next one at {again}. Options already checked this round are free.")]
        self.new_checks[sender] = [*recent, now]
        self.verdicts[option] = self._fits(option, self.applied)
        self.ledger.append(f"Round {self.round}, {_clock(now)}: {self._name(sender)} checked {option}: "
                           f"{'fits everyone' if self.verdicts[option] else 'does not fit everyone'}")
        return [self._say(self._verdict_text(option))]

    def _vote(self, sender: str, option: str, verb: str) -> list[Outgoing]:
        if sender not in self.members:
            return [self._say("Only the friends on this trip can vote.")]
        if not self.verdicts.get(option):
            return [self._say(f"{option} needs a ✅ this round before anyone can vote on it. Try: check {option}")]
        self.votes.setdefault(option, {})[sender] = verb
        ins = [m for m, v in self.votes[option].items() if v == "in"]
        text = f"{self._name(sender)} is {verb} on {option}. {len(ins)} of {len(self.members)} in."
        if len(ins) == len(self.members):
            text += f" Everyone is in. {self._name(self.organizer)}: type `lock {option}`."
        return [self._say(text)]

    def _lock(self, sender: str, option: str) -> list[Outgoing]:
        if sender != self.organizer:
            return [self._say(f"Only {self._name(self.organizer)} (the organizer) can lock the trip.")]
        ins = [m for m, v in self.votes.get(option, {}).items() if v == "in"]
        if len(ins) < len(self.members):
            return [self._say(f"Not everyone is in on {option} yet ({len(ins)} of {len(self.members)}).")]
        latest = {m: self.staged.get(m, Limits()) for m in self.members}
        if not self._fits(option, latest):  # a frozen ✅ must never lock a trip that no longer fits someone
            self.ledger.append(f"Round {self.round}: lock {option} refused (no longer fits everyone)")
            return [self._say(f"Can't lock {option}: it no longer fits everyone."), *self._start_round()]
        self.locked = option
        self.ledger.append(f"Round {self.round}: {self._name(sender)} locked {option}")
        return [self._say(f"🔒 Locked: {self._label(option)}. It fits everyone, and everyone said yes.")]

    def _new_round(self, sender: str) -> list[Outgoing]:
        if sender != self.organizer:
            return [self._say(f"Only {self._name(self.organizer)} (the organizer) can start a new round.")]
        return self._start_round()

    def _start_round(self) -> list[Outgoing]:
        reopened = self.locked
        self.applied = dict(self.staged)
        self.verdicts, self.votes, self.locked = {}, {}, None
        self.round += 1
        self.ledger.append(f"Round {self.round} started" + (f" (reopened after {reopened} was locked)" if reopened else ""))
        note = f" The trip was locked on {reopened}; it's open again." if reopened else ""
        return [self._say(f"Round {self.round}: everyone's latest limits now apply. Check options again.{note}")]

    # ── Rules ──────────────────────────────────────────────────────────────────────────────────────────────────

    def _not_ready(self) -> str | None:
        if len(self.members) < self.min_group:
            return (f"❌ The treaty needs at least {self.min_group} friends (with 2, any answer would reveal the other "
                    "person's limit).")
        ready = sum(1 for m in self.members if self.applied.get(m, Limits()).budget)
        if ready < len(self.members):
            return (f"❌ Not yet: {ready} of {len(self.members)} friends have set their limits privately. Each friend "
                    "can tell their own @treaty in their private channel.")
        return None

    def _fits(self, option: str, limits: dict[str, Limits]) -> bool:
        o = self.options[option]
        start, end = date.fromisoformat(o["start"]), date.fromisoformat(o["end"])
        tags = {t.lower() for t in o.get("tags", [])}
        for member in self.members:
            mine = limits.get(member, Limits())
            if mine.budget is None or o["cost"] > mine.budget:
                return False
            if mine.window and not (mine.window[0] <= start and end <= mine.window[1]):
                return False
            if mine.excluded & tags:
                return False
        return True

    # ── Words ──────────────────────────────────────────────────────────────────────────────────────────────────

    def _verdict_text(self, option: str) -> str:
        mark = "✅ fits everyone" if self.verdicts[option] else "❌ doesn't fit everyone"
        return f"{self._label(option)}: {mark}."

    def _list_options(self) -> str:
        lines = [f"{oid}: {o['name']}, {self._money(o['cost'])} per person, "
                 f"{date.fromisoformat(o['start']):%d %b} to {date.fromisoformat(o['end']):%d %b}"
                 + (f" ({', '.join(o.get('tags', []))})" if o.get("tags") else "")
                 for oid, o in self.options.items()]
        return "Trip options:\n" + "\n".join(lines)

    def _status(self) -> str:
        ready = sum(1 for m in self.members if self.applied.get(m, Limits()).budget)
        fits = [o for o, ok in self.verdicts.items() if ok]
        return (f"Round {self.round}. {ready} of {len(self.members)} friends ready. "
                f"Fits everyone so far: {', '.join(fits) or 'none checked yet'}."
                + (f" Locked: {self.locked}." if self.locked else ""))

    def _tally(self) -> str:
        if not self.votes:
            return "No votes yet this round."
        return "\n".join(f"{o}: {sum(v == 'in' for v in vs.values())} in, {sum(v == 'out' for v in vs.values())} out"
                         for o, vs in self.votes.items())

    def _label(self, option: str) -> str:
        return f"{option} ({self.options[option]['name']})"

    def _name(self, member: str) -> str:
        return self.members.get(member, "the guide" if member == self.guide and self.guide else "someone")

    def _money(self, amount: int | None) -> str:
        symbol = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(self.currency, "")
        return f"{symbol}{amount:,}" if symbol else f"{amount:,} {self.currency}".strip()

    def _say(self, text: str) -> Outgoing:
        return (TRIP, None, text)

    def _say_private(self, owner: str, text: str) -> Outgoing:
        return (PRIVATE, owner, text)


def _words(text: str) -> list[str]:
    return text.strip().lower().split()


def _parse_amount(text: str) -> int | None:
    cleaned = text.replace(",", "").replace("₹", "").replace("$", "").strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(k?)", cleaned)
    if not match:
        return None
    amount = float(match.group(1)) * (1000 if match.group(2) else 1)
    return int(amount) if 1 <= amount <= 100_000_000 else None


def _parse_brief(body: str) -> dict[str, Any] | None:
    parts = [p.strip() for p in body.strip().lstrip("📝 ").split("|")]
    if not parts or parts[0].lower() != "brief":
        return None
    brief: dict[str, Any] = {"prefs": {}}
    for part in parts[1:]:
        key, _, value = part.partition(":")
        key, value = key.strip().lower(), value.strip()
        if not value or value.lower() in ("none", "-", "n/a"):
            if key == "avoid":
                brief["avoid"] = frozenset()
            continue
        if key == "budget":
            amounts = [a for a in (_parse_amount(m) for m in re.findall(r"[\d.,]+\s*k?", value.lower())) if a]
            if amounts:
                brief["budget"] = max(amounts)  # "around 10-12k": the most this friend will spend
        elif key == "dates":
            window = _parse_window(value)
            if window:
                brief["window"] = window
        elif key == "avoid":
            brief["avoid"] = frozenset(t.strip().lower() for t in value.split(",") if t.strip())
        elif key in ("food", "hotel", "flights"):
            brief["prefs"][key] = value.lower()
    return brief


def _parse_option(body: str) -> dict[str, Any] | None:
    parts = [p.strip() for p in body.strip().split("|")]
    head = parts[0].split()
    if len(parts) < 4 or len(head) != 2 or head[0].lower() != "option" or not re.fullmatch(r"[A-Za-z]\d{1,2}", head[1]):
        return None
    cost = _parse_amount(parts[2])
    window = _parse_window(parts[3])
    if cost is None or window is None:
        return None
    tags = []
    if len(parts) > 4 and parts[4].lower().startswith("tags"):
        tags = [t.strip().lower() for t in parts[4].partition(":")[2].split(",") if t.strip()]
    return {"id": head[1].upper(), "name": parts[1], "cost": cost, "start": window[0].isoformat(),
            "end": window[1].isoformat(), "tags": tags}


def _terms(option: dict[str, Any]) -> tuple:
    return option["cost"], option["start"], option["end"], tuple(sorted(option.get("tags", [])))


def _parse_window(text: str) -> tuple[date, date] | None:
    found = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    try:
        if len(found) == 2:
            first, last = date.fromisoformat(found[0]), date.fromisoformat(found[1])
        else:
            match = re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,2})\s+([a-z]{3})[a-z]*(?:\s+(\d{4}))?", text.strip())
            if not match:
                return None
            year = int(match.group(4) or datetime.now(timezone.utc).year)
            month = MONTHS[match.group(3)]
            first, last = date(year, month, int(match.group(1))), date(year, month, int(match.group(2)))
    except (ValueError, KeyError):
        return None
    return (first, last) if first <= last else None


def _clock(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")
