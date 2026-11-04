"""The seven keeper tests from docs/design.md, plus the privacy invariants. Test data only."""

import itertools
import re

from treaty.core import NEW_CHECKS_PER_WINDOW, PRIVATE, TRIP, WINDOW_MS, Treaty

CATALOG = {
    "currency": "INR",
    "options": [
        {"id": "A1", "name": "Option A1", "cost": 6000, "start": "2026-12-12", "end": "2026-12-14", "tags": ["train"]},
        {"id": "B2", "name": "Option B2", "cost": 8000, "start": "2026-12-12", "end": "2026-12-15", "tags": ["train"]},
        {"id": "C3", "name": "Option C3", "cost": 9500, "start": "2026-12-19", "end": "2026-12-21", "tags": ["flight"]},
        {"id": "D4", "name": "Option D4", "cost": 7000, "start": "2026-12-12", "end": "2026-12-13", "tags": ["bus"]},
        {"id": "E5", "name": "Option E5", "cost": 5000, "start": "2026-12-12", "end": "2026-12-13", "tags": ["train"]},
        {"id": "F6", "name": "Option F6", "cost": 4000, "start": "2026-12-12", "end": "2026-12-13", "tags": ["train"]},
    ],
}
FRIENDS = {"alice": "Alice", "bob": "Bob", "chen": "Chen"}
BUDGETS = {"alice": 9000, "bob": 8500, "chen": 12000}


class Trip:
    def __init__(self, friends=FRIENDS, organizer="alice"):
        self.treaty = Treaty(CATALOG, dict(friends), organizer)
        self.ids = itertools.count(1)
        self.clock = 1_800_000_000_000
        self.history = []
        self.trip_texts = []

    def send(self, room, sender, body, owner=None, wall=(True, ""), event_id=None, advance_ms=1000):
        self.clock += advance_ms
        event = {"id": event_id or f"m{next(self.ids)}", "room": room, "owner": owner, "sender": sender,
                 "body": body, "ts": self.clock}
        self.history.append(event)
        out = self.treaty.handle({**event, "wall": wall})
        self.trip_texts += [text for r, _, text in out if r == TRIP]
        return out

    def private(self, who, body, **kw):
        return self.send(PRIVATE, who, body, owner=who, **kw)

    def trip(self, who, body, **kw):
        return self.send(TRIP, who, body, **kw)

    def set_budgets(self, budgets=BUDGETS):
        for who, amount in budgets.items():
            self.private(who, f"budget {amount}")


def texts(out):
    return "\n".join(t for _, _, t in out)


def test_1_raw_numbers_are_refused_only_catalog_ids_are_checked():
    t = Trip()
    t.set_budgets()
    for probe in ("check 9200", "check $9200", "check ₹8999"):
        assert "Only catalog options" in texts(t.trip("bob", probe))
    assert "✅ fits everyone" in texts(t.trip("bob", "check B2"))  # 8000 fits 9000, 8500, 12000
    assert "❌ doesn't fit everyone" in texts(t.trip("bob", "check C3"))  # 9500 is over Bob's 8500


def test_2_no_verdict_with_two_friends_or_before_everyone_is_ready():
    pair = Trip(friends={"alice": "Alice", "bob": "Bob"})
    pair.set_budgets({"alice": 9000, "bob": 8500})
    assert "at least 3 friends" in texts(pair.trip("bob", "check B2"))
    t = Trip()
    t.set_budgets({"alice": 9000, "bob": 8500})  # Chen hasn't set one yet
    assert "2 of 3 friends" in texts(t.trip("bob", "check B2"))


def test_3_mid_round_change_is_staged_and_lock_rechecks_latest_limits():
    t = Trip()
    t.set_budgets()
    assert "✅" in texts(t.trip("bob", "check B2"))
    reply = texts(t.private("alice", "budget 7500"))  # now below B2's 8000
    assert "takes effect next round" in reply
    assert "✅" in texts(t.trip("chen", "check B2"))  # same round: still the frozen verdict
    assert "✅" in texts(t.trip("bob", "check D4"))  # new checks this round also use the applied limits (7000 ≤ 9000)
    for who in FRIENDS:
        t.trip(who, "in B2")
    out = texts(t.trip("alice", "lock B2"))
    assert "Can't lock B2" in out and "Round 2" in out  # the lock re-checks Alice's latest 7500
    assert t.treaty.locked is None
    assert "❌" in texts(t.trip("bob", "check B2"))  # round 2 uses the latest limits


def test_4_replaying_history_rebuilds_the_same_state():
    t = Trip()
    t.set_budgets()
    t.private("bob", "dates 2026-12-10 2026-12-20")
    t.private("chen", "no flight")
    t.trip("bob", "check B2")
    t.private("alice", "budget 8200")
    for who in FRIENDS:
        t.trip(who, "in B2")
    rebuilt = Treaty(CATALOG, dict(FRIENDS), "alice")
    rebuilt.replay(t.history)
    for attribute in ("applied", "staged", "round", "verdicts", "votes", "locked", "new_checks"):
        assert getattr(rebuilt, attribute) == getattr(t.treaty, attribute), attribute


def test_5_a_duplicate_event_is_handled_once():
    t = Trip()
    t.set_budgets()
    assert t.trip("bob", "check B2", event_id="dup")
    assert t.trip("bob", "check B2", event_id="dup") == []
    assert len(t.treaty.ledger) == 1


def test_6_an_intruder_in_a_private_room_suspends_checks():
    t = Trip()
    t.set_budgets()
    out = texts(t.trip("bob", "check B2", wall=(False, "treaty-planner joined #treaty-alice")))
    assert "Wall breach" in out and "treaty-planner" in out
    assert t.treaty.verdicts == {} and t.treaty.new_checks == {}  # no verdict, no check counted
    assert "✅" in texts(t.trip("bob", "check B2"))  # resumes once the wall is intact


def test_7_rate_limit_counts_new_checks_only_and_resets():
    t = Trip()
    t.set_budgets()
    ids = ["A1", "B2", "C3", "D4", "E5", "F6"]
    for option in ids[:NEW_CHECKS_PER_WINDOW]:
        assert "Only" not in texts(t.trip("bob", f"check {option}"))
    refused = texts(t.trip("bob", f"check {ids[5]}"))
    assert "used 5 new checks" in refused and "F6" not in t.treaty.verdicts  # refused, and nothing learned
    assert "fits everyone" in texts(t.trip("bob", "check A1"))  # a repeat is free
    t.clock += WINDOW_MS
    assert "fits everyone" in texts(t.trip("bob", f"check {ids[5]}"))  # the window has passed: allowed again

    other = Trip()
    other.set_budgets()
    for option in ids[:NEW_CHECKS_PER_WINDOW]:
        other.trip("bob", f"check {option}")
    assert "fits everyone" in texts(other.trip("chen", f"check {ids[5]}"))  # the limit is per person


def test_private_replies_stay_private_and_no_limit_reaches_the_trip_channel():
    t = Trip()
    t.set_budgets()
    t.private("bob", "dates 2026-12-10 2026-12-20")
    for option in ("A1", "B2", "C3"):
        t.trip("chen", f"check {option}")
    t.trip("alice", "status")
    t.private("bob", "show")
    trip_numbers = set(re.findall(r"\d[\d,]*", " ".join(t.trip_texts).replace(",", "")))
    assert not trip_numbers & {"9000", "8500", "12000"}
    assert all(r == PRIVATE for r, _, txt in t.private("alice", "budget 9100"))


def test_only_the_rooms_own_friend_can_set_its_limits_and_only_the_organizer_locks():
    t = Trip()
    assert t.send(PRIVATE, "bob", "budget 100", owner="alice") == []  # Bob writing in Alice's channel: ignored
    t.set_budgets()
    t.trip("bob", "check B2")
    for who in FRIENDS:
        t.trip(who, "in B2")
    assert "Only Alice" in texts(t.trip("bob", "lock B2"))
    assert "Locked" in texts(t.trip("alice", "lock B2"))
    assert "locked" in texts(t.trip("bob", "check C3"))
