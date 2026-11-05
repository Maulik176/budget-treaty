"""treaty-keeper: the Treaty run as a Switch agent on Discord.

    uv run treaty-keeper register                 # once; needs ~/.config/budget-treaty/registration_key
    uv run treaty-keeper whois --room "<room>"    # who Switch sees in a room (to fill in trip.json)
    uv run treaty-keeper run --config trip.json

trip.json names the shared room, each friend's Discord username and private room, the organizer and the catalog.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from treaty.core import PRIVATE, TRIP, Treaty
from treaty.switch_client import AGENT_FILE, Switch, SwitchError, hold_room, listen, register

KEEPER_DESCRIPTION = "Budget Treaty keeper: holds each friend's private limits and checks trip options. Plain code, no AI."
CATALOG_DOC, LEDGER_DOC, PREFERENCES_DOC = "trip-catalog", "treaty-ledger", "group-preferences"
DOCS = {
    CATALOG_DOC: ("The trip packages and their prices",
                  "The only source of trip packages and prices. Quote packages by id. Never invent a price."),
    LEDGER_DOC: ("Who checked which package, the verdicts, the votes and the lock",
                 "The trip's public history. Use it to summarize what the group has checked and decided. It never "
                 "contains anyone's limits."),
    PREFERENCES_DOC: ("What the group prefers, as counts with no names",
                      "Food, hotel and flight preferences across the group, and how many friends are ready. Use it to "
                      "shape packages. It never contains names, budgets or dates."),
}


# ── Pure helpers (tested offline) ──────────────────────────────────────────────────────────────────────────────

def to_event(entry: dict[str, Any], room: str, owner: str | None) -> dict[str, Any]:
    """A Switch message (history entry or live payload) as a Treaty event. Members are keyed by username."""
    return {"id": entry.get("id") or entry.get("message_id"), "room": room, "owner": owner,
            "sender": entry.get("sender_name") or "", "body": entry.get("body") or "", "ts": entry.get("timestamp", 0)}


def merged_history(histories: list[tuple[list[dict[str, Any]], str, str | None]], own_name: str) -> list[dict[str, Any]]:
    """Every room's messages as events, in timestamp order, without the keeper's own posts."""
    events = [to_event(e, room, owner) for entries, room, owner in histories for e in entries
              if e.get("sender_name") != own_name]
    return sorted(events, key=lambda e: e["ts"])


def intruders(participants: list[dict[str, Any]], keeper_agent_id: str, friend: str,
              companion: str | None = None) -> list[str]:
    """In a private room only the keeper (by agent id), that friend (by username) and that friend's own companion
    (by agent name) may be present."""
    found = []
    for p in participants:
        if p.get("type") == "agent" and p.get("agent_id") != keeper_agent_id and p.get("name") != companion:
            found.append(f"agent {p.get('name')}")
        elif p.get("type") == "user" and p.get("name") != friend:
            found.append(f"person {p.get('name')}")
    return found


def catalog_text(options: dict[str, dict[str, Any]], currency: str) -> str:
    if not options:
        return f"Currency: {currency}. No packages yet: the guide registers them with OPTION lines."
    lines = [f"Currency: {currency}", "Packages (the only valid prices and ids):"]
    for o in options.values():
        lines.append(f"- {o['id']}: {o['name']}. {o['cost']} per person. {o['start']} to {o['end']}. "
                     f"Tags: {', '.join(o.get('tags', [])) or 'none'}. {o.get('summary', '')}".rstrip())
    return "\n".join(lines)


# ── The adapter ────────────────────────────────────────────────────────────────────────────────────────────────

class Keeper:
    def __init__(self, switch: Switch, config: dict[str, Any], catalog: dict[str, Any]):
        self.switch = switch
        self.catalog = catalog
        rooms = {r["name"]: r["room_id"] for r in switch.rooms()}
        missing = [n for n in [config["trip_room"], *(f["room"] for f in config["friends"])] if n not in rooms]
        if missing:
            raise SwitchError(f"the keeper isn't in these rooms yet: {missing}. "
                              f"Invite it in each channel with: !invite-agent @{switch.agent['name']}")
        self.trip_room = rooms[config["trip_room"]]
        self.private_rooms = {rooms[f["room"]]: f["discord"] for f in config["friends"]}  # room id -> friend
        self.room_of = {friend: room for room, friend in self.private_rooms.items()}
        self.companions = {f["discord"]: f["companion"] for f in config["friends"] if f.get("companion")}
        self.treaty = Treaty(catalog, {f["discord"]: f["name"] for f in config["friends"]}, config["organizer"],
                             min_group=config.get("min_group", 3), companions=dict(self.companions),
                             guide=config.get("guide", ""))
        started = config.get("trip_started_at")  # ISO time: ignore older messages (a fresh trip, same channels)
        self.trip_since = int(datetime.fromisoformat(started).timestamp() * 1000) if started else 0
        self.own_posts: set[str] = set()
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.room_ready = asyncio.Event()
        self.doc_ids: dict[str, str] = {}
        self.doc_content: dict[str, str] = {}

    def rebuild(self) -> int:
        """Switch's room history is the only record: replay it through the same rules, posting nothing."""
        def since(room: str) -> list[dict[str, Any]]:  # a fresh trip ignores older messages in every room
            return [e for e in self.switch.history(room) if e.get("timestamp", 0) >= self.trip_since]

        histories = [(since(self.trip_room), TRIP, None)]
        histories += [(since(room), PRIVATE, friend) for room, friend in self.private_rooms.items()]
        events = merged_history(histories, self.switch.agent["name"])
        self.treaty.replay(events)
        return len(events)

    def wall(self) -> tuple[bool, str]:
        for room, friend in self.private_rooms.items():
            found = intruders(self.switch.participants(room), self.switch.agent["id"], friend,
                              self.companions.get(friend))
            if found:
                return False, f"{', '.join(found)} in {self.treaty.members[friend]}'s private channel"
        return True, ""

    def _documents(self) -> dict[str, str]:
        t = self.treaty
        return {CATALOG_DOC: catalog_text(t.options, t.currency),
                LEDGER_DOC: "\n".join(t.ledger) or "Nothing checked yet.",
                PREFERENCES_DOC: t.preferences_text()}

    def ensure_documents(self) -> None:
        """The keeper's three room documents in the trip room. Only their creator can change them."""
        ops = self.switch.agent["ops_connection_id"]
        room = self.switch.op("connect_to_room", ops, room_id=self.trip_room, include_general_instructions=False)
        self.doc_ids = {d["name"]: d["id"] for d in room.get("documents", [])
                        if d.get("created_by_agent_id") == self.switch.agent["id"] and d["name"] in DOCS}
        for name, content in self._documents().items():
            if name not in self.doc_ids:
                description, instructions = DOCS[name]
                self.doc_ids[name] = self.switch.op("create_room_document", ops, name=name, content=content,
                                                    description=description, instructions=instructions)["document_id"]
            else:
                self.switch.op("update_room_document", ops, document_id=self.doc_ids[name], content=content)
            self.doc_content[name] = content

    def sync_documents(self) -> None:
        for name, content in self._documents().items():
            if name in self.doc_ids and content != self.doc_content.get(name):
                self.switch.op("update_room_document", self.switch.agent["ops_connection_id"],
                               document_id=self.doc_ids[name], content=content)
                self.doc_content[name] = content

    async def on_frame(self, frame: dict[str, Any]) -> None:
        await self.queue.put(frame)

    async def work(self) -> None:
        while True:
            frame = await self.queue.get()
            try:
                await self._process(frame)
            except Exception as error:  # one bad event must not stop the trip
                print(f"Error handling event {frame.get('id')}: {error!r}")

    async def _process(self, frame: dict[str, Any]) -> None:
        data, payload = frame.get("data") or {}, (frame.get("data") or {}).get("payload") or {}
        room_id = data.get("room_id")
        if payload.get("message_id") in self.own_posts or payload.get("sender_name") == self.switch.agent["name"]:
            return
        if room_id == self.trip_room:
            event = to_event(payload, TRIP, None)
        elif room_id in self.private_rooms:
            event = to_event(payload, PRIVATE, self.private_rooms[room_id])
        else:
            return
        if event["room"] == TRIP and event["body"].strip().lower().startswith(("check", "option")):
            event["wall"] = await asyncio.to_thread(self.wall)
        for room, owner, text in await asyncio.to_thread(self.treaty.handle, event):
            target = self.trip_room if room == TRIP else self.room_of[owner]
            posted = await asyncio.to_thread(self.switch.post, target, text)
            if posted:
                self.own_posts.add(posted)
        await asyncio.wait_for(self.room_ready.wait(), timeout=15)
        await asyncio.to_thread(self.sync_documents)

    async def run(self) -> None:
        listener = asyncio.create_task(listen(self.switch, self.on_frame))  # open first, so nothing slips past the rebuild
        holder = asyncio.create_task(hold_room(self.switch, self.trip_room, self.switch.agent["ops_connection_id"],
                                               self.room_ready, self.on_frame))
        replayed = await asyncio.to_thread(self.rebuild)
        await asyncio.wait_for(self.room_ready.wait(), timeout=15)
        await asyncio.to_thread(self.ensure_documents)
        print(f"Rebuilt from {replayed} messages in Switch history. Round {self.treaty.round}. Keeper is live.")
        await asyncio.gather(listener, holder, self.work())


def main() -> None:
    parser = argparse.ArgumentParser(prog="treaty-keeper")
    commands = parser.add_subparsers(dest="command", required=True)
    reg = commands.add_parser("register", help="register the keeper with Switch (once)")
    reg.add_argument("--name", default="treaty-keeper")
    who = commands.add_parser("whois", help="list who Switch sees in a room")
    who.add_argument("--room", required=True)
    go = commands.add_parser("run", help="run the keeper for one trip")
    go.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        if args.command == "register":
            agent = register(args.name, KEEPER_DESCRIPTION)
            print(f"Registered '{agent['name']}'. Its key is saved in {AGENT_FILE} (not shown).")
            return
        if not AGENT_FILE.exists():
            sys.exit("The keeper isn't registered yet. Run: uv run treaty-keeper register")
        agent = json.loads(AGENT_FILE.read_text())
        switch = Switch(agent["base"], agent)
        if args.command == "whois":
            rooms = {r["name"]: r["room_id"] for r in switch.rooms()}
            if args.room not in rooms:
                sys.exit(f"Not in that room. Rooms the keeper is in: {', '.join(rooms) or 'none'}")
            for p in switch.participants(rooms[args.room]):
                print(f"{p['type']:6} {p['name']}")
            return
        config = json.loads(Path(args.config).read_text())
        catalog = json.loads((Path(args.config).parent / config["catalog"]).read_text())
        asyncio.run(Keeper(switch, config, catalog).run())
    except SwitchError as error:
        sys.exit(str(error))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
