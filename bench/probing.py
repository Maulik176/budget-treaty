"""How much can a nosy friend learn about the group's budgets by asking for checks?

An attacker wants the group's lowest budget (the number that decides every "doesn't fit"). We give them the best
strategy each design allows and measure what they learn. Deterministic: budgets come from a fixed seed.

    uv run python bench/probing.py
"""

from __future__ import annotations

import itertools
import math
import random
from statistics import mean

from treaty.core import NEW_CHECKS_PER_WINDOW, PRIVATE, TRIP, WINDOW_MS, Treaty

LOW, HIGH = 3_000, 30_000  # plausible per-person trip budgets, in rupees
FRIENDS = {"alice": "Alice", "bob": "Bob", "chen": "Chen", "dev": "Dev"}
TRIALS = 200


def catalog(prices: list[int]) -> dict:
    return {"currency": "INR", "options": [
        {"id": f"O{i}", "name": f"Option {i}", "cost": p, "start": "2026-12-12", "end": "2026-12-14", "tags": []}
        for i, p in enumerate(prices, 1)]}


PRICES = [5_000, 7_500, 10_000, 14_000, 19_000, 25_000]  # a realistic six-option catalog


def naive_per_person(budget: int) -> tuple[int, int]:
    """A bot that answers 'does ₹X fit Bob?' Binary search finds Bob's exact budget."""
    lo, hi, asks = LOW, HIGH, 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        asks += 1
        lo, hi = (mid, hi) if mid <= budget else (lo, mid - 1)
    return asks, 0  # exact: zero uncertainty


def naive_group_amounts(minimum: int) -> tuple[int, int]:
    """Group-only answers, but any typed amount: binary search still finds the group's exact minimum."""
    return naive_per_person(minimum)


def treaty_attack(budgets: dict[str, int]) -> tuple[int, int, int]:
    """The real keeper (catalog only, group verdict, rate limit). The attacker checks every option it is allowed to.

    Returns (checks answered, minutes spent, width in rupees of what the attacker can say about the minimum).
    """
    treaty = Treaty(catalog(PRICES), dict(FRIENDS), "alice")
    ids, clock = itertools.count(), 1_800_000_000_000
    for who, amount in budgets.items():
        treaty.handle({"id": f"p{next(ids)}", "room": PRIVATE, "owner": who, "sender": who,
                       "body": f"budget {amount}", "ts": clock})
    answered, start = 0, clock
    for option in [f"O{i}" for i in range(1, len(PRICES) + 1)]:
        while True:
            clock += 1_000
            out = treaty.handle({"id": f"t{next(ids)}", "room": TRIP, "owner": None, "sender": "bob",
                                 "body": f"check {option}", "ts": clock, "wall": (True, "")})
            if "new checks in 10 minutes" in out[0][2]:
                clock += WINDOW_MS  # wait out the window, then try again
                continue
            answered += 1
            break
    fits = sorted(PRICES[int(o[1:]) - 1] for o, ok in treaty.verdicts.items() if ok)
    fails = sorted(PRICES[int(o[1:]) - 1] for o, ok in treaty.verdicts.items() if not ok)
    lower = fits[-1] if fits else LOW  # the minimum is at least the dearest option that fits
    upper = (fails[0] - 1) if fails else HIGH  # and below the cheapest option that doesn't
    return answered, math.ceil((clock - start) / 60_000), upper - lower


def main() -> None:
    rng = random.Random(42)
    rows = {"per": [], "group": [], "treaty": []}
    for _ in range(TRIALS):
        budgets = {f: rng.randint(LOW, HIGH) for f in FRIENDS}
        rows["per"].append(naive_per_person(budgets["alice"]))
        rows["group"].append(naive_group_amounts(min(budgets.values())))
        rows["treaty"].append(treaty_attack(budgets))
    per_checks = mean(r[0] for r in rows["per"])
    group_checks = mean(r[0] for r in rows["group"])
    t_checks = mean(r[0] for r in rows["treaty"])
    t_minutes = mean(r[1] for r in rows["treaty"])
    t_width = mean(r[2] for r in rows["treaty"])
    print(f"{TRIALS} random groups of {len(FRIENDS)}, budgets ₹{LOW:,}–₹{HIGH:,}, catalog {PRICES}\n")
    print(f"{'Design':58} {'Checks':>7} {'Minutes':>8} {'Attacker learns':>24}")
    print(f"{'Bot answers per person, any amount':58} {per_checks:7.1f} {'—':>8} {'one friend exactly (₹0)':>24}")
    print(f"{'Group verdict, any amount':58} {group_checks:7.1f} {'—':>8} {'lowest budget exactly':>24}")
    print(f"{'Budget Treaty (catalog only, group verdict, 5 per 10 min)':58} {t_checks:7.1f} {t_minutes:8.0f} "
          f"{'a range ~₹' + format(round(t_width), ',') + ' wide':>24}")
    print("\nThe Treaty attacker also never learns whose budget it is, and the ledger shows every check they made.")


if __name__ == "__main__":
    main()
