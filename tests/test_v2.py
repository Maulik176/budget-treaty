"""v2: each friend's own companion writes their brief; the guide registers packages; votes in plain words."""

import itertools
import re

from treaty.core import PRIVATE, TRIP, Treaty

FRIENDS = {"maulik": "Maulik", "aarav": "Aarav", "diya": "Diya"}
COMPANIONS = {"maulik": "treaty-maulik", "aarav": "treaty-aarav", "diya": "treaty-diya"}
GUIDE = "treaty-planner"


class Bali:
    def __init__(self):
        self.treaty = Treaty({"currency": "INR", "options": []}, dict(FRIENDS), "maulik", companions=dict(COMPANIONS),
                             guide=GUIDE)
        self.ids = itertools.count(1)
        self.clock = 1_800_000_000_000
        self.trip_texts = []

    def send(self, room, sender, body, owner=None):
        self.clock += 1000
        out = self.treaty.handle({"id": f"m{next(self.ids)}", "room": room, "owner": owner, "sender": sender,
                                  "body": body, "ts": self.clock, "wall": (True, "")})
        self.trip_texts += [t for r, _, t in out if r == TRIP]
        return out

    def brief(self, who, body):
        return self.send(PRIVATE, COMPANIONS[who], body, owner=who)

    def everyone_briefed(self):
        self.brief("maulik", "BRIEF | budget: 60,000 to 75,000 | dates: 2026-12-10 to 2026-12-20 | avoid: none | "
                             "food: non-veg | hotel: mid-range | flights: cheapest")
        self.brief("aarav", "BRIEF | budget: around 70k | dates: 2026-12-08 to 2026-12-22 | food: any | hotel: budget | "
                            "flights: cheapest")
        return self.brief("diya", "📝 BRIEF | budget: 65000 | dates: 2026-12-11 to 2026-12-19 | food: non-veg | "
                                  "hotel: mid-range | flights: any")


def texts(out):
    return "\n".join(t for _, _, t in out)


def test_the_friends_own_companion_writes_the_brief_and_nobody_else_can():
    b = Bali()
    out = b.brief("maulik", "BRIEF | budget: 10-12k | dates: 2026-12-10 to 2026-12-20 | food: non-veg")
    assert "Saved your brief" in texts(out) and "your companion and the keeper" in texts(out)
    assert all(r == PRIVATE for r, _, _ in out)
    assert b.treaty.applied["maulik"].budget == 12000  # "10-12k": the most Maulik will spend
    assert b.send(PRIVATE, "treaty-aarav", "BRIEF | budget: 100", owner="maulik") == []  # Aarav's companion, wrong room
    assert b.send(PRIVATE, GUIDE, "BRIEF | budget: 100", owner="maulik") == []
    assert b.brief("maulik", "Sounds great! Anything else you'd like?") == []  # companion chatting, not a brief
    assert b.treaty.applied["maulik"].budget == 12000


def test_the_trip_channel_hears_when_everyone_is_ready_but_no_numbers():
    b = Bali()
    b.brief("maulik", "BRIEF | budget: 12000")
    b.brief("aarav", "BRIEF | budget: 70000")
    assert not b.trip_texts
    out = b.brief("diya", "BRIEF | budget: 65000")
    assert "All 3 friends have set their limits privately" in texts(out)
    assert b.brief("diya", "BRIEF | budget: 66000") and len(b.trip_texts) == 1  # announced once


def test_the_guide_registers_packages_and_each_is_checked_at_once():
    b = Bali()
    b.everyone_briefed()
    out = texts(b.send(TRIP, GUIDE, "OPTION P1 | Bali 6 days: Ubud + Seminyak, mid-range | 64000 | "
                                    "2026-12-12 to 2026-12-18 | tags: flight, mid-range"))
    assert "P1 (Bali 6 days: Ubud + Seminyak, mid-range): ✅ fits everyone" in out  # 64000 fits 75000, 70000 and 65000
    out = texts(b.send(TRIP, GUIDE, "OPTION P2 | Bali 8 days, beach resort | 90000 | 2026-12-12 to 2026-12-20"))
    assert "❌ doesn't fit everyone" in out
    assert "already checked this round" in texts(
        b.send(TRIP, GUIDE, "OPTION P1 | Bali 6 days | 60000 | 2026-12-12 to 2026-12-18 | tags: flight, mid-range"))
    assert b.send(TRIP, "aarav", "OPTION P3 | cheap | 1000 | 2026-12-12 to 2026-12-13") == []  # only the guide adds
    assert "P3" not in b.treaty.options and b.treaty.catalog_version == 2


def test_votes_and_the_lock_work_in_plain_words():
    b = Bali()
    b.everyone_briefed()
    b.send(TRIP, GUIDE, "OPTION P1 | Bali 6 days | 64000 | 2026-12-12 to 2026-12-18 | tags: flight")
    assert "Maulik is in on P1" in texts(b.send(TRIP, "maulik", "P1 looks great, count me in"))
    assert "Aarav is in on P1" in texts(b.send(TRIP, "aarav", "yes to P1 🙌"))
    assert "Diya is out on P1" in texts(b.send(TRIP, "diya", "not for me, P1 feels rushed"))
    assert "Diya is in on P1" in texts(b.send(TRIP, "diya", "ok fine, I'm in for P1"))
    assert b.send(TRIP, "aarav", "should we lock P1?") == []  # only the organizer locks; a question isn't a vote
    assert "Locked" in texts(b.send(TRIP, "maulik", "let's lock P1!"))


def test_preferences_are_shared_as_counts_without_names_or_money():
    b = Bali()
    b.everyone_briefed()
    summary = b.treaty.preferences_text()
    assert "non-veg ×2" in summary and "any ×1" in summary and "cheapest ×2" in summary
    assert not re.search(r"Maulik|Aarav|Diya|75000|75,000|70000|65000", summary)


def test_the_brief_in_budget_wording_means_the_cap():
    b = Bali()
    for wording, cap in (("around 10k", 10000), ("10,000 to 12,000", 12000), ("₹45,000", 45000), ("1.2 lakh", None)):
        b.brief("maulik", f"BRIEF | budget: {wording}")
        if cap:
            assert b.treaty.staged["maulik"].budget == cap, wording
