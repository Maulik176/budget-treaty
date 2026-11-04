"""A small client for Switch's agent protocol (switch-core 0.31.0). Ported from the PlotTwist engine, tested live.

- One `scope=all` WebSocket hears every room the agent belongs to. Pings are answered before any work.
- Replies go out with `POST /agents/{id}/message`, which needs no room claim.
- Room-scoped operations (room documents) need a second socket that claims that one room; its connection id goes in
  the `X-Switch-Connection-Id` header.

Secrets live in ~/.config/budget-treaty/ with mode 600 and are never printed.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import websockets

CONFIG = Path.home() / ".config" / "budget-treaty"
REGISTRATION_KEY = CONFIG / "registration_key"
AGENT_FILE = CONFIG / "agent.json"
DEFAULT_BASE = "http://localhost:57371"
STOP_CLOSE_CODES = {4400, 4401, 4403}  # bad request, bad key, room can't be served: retrying won't help


class SwitchError(RuntimeError):
    pass


def save_private(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


class Switch:
    def __init__(self, base: str, agent: dict[str, Any] | None = None):
        self.base = base.rstrip("/")
        self.agent = agent

    def request(self, method: str, path: str, body: Any = None, token: str | None = None,
                connection_id: str | None = None) -> Any:
        key = token or (self.agent or {}).get("api_key")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        if connection_id:
            headers["X-Switch-Connection-Id"] = connection_id
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise SwitchError(f"{method} {path} -> {error.code}: {error.read().decode(errors='replace')[:300]}") from None
        except urllib.error.URLError as error:
            raise SwitchError(f"can't reach Switch at {self.base}: {error.reason}") from None
        return json.loads(raw) if raw else None

    def op(self, _operation: str, _connection: str | None = None, **args: Any) -> Any:
        return self.request("POST", f"/agents/{self.agent['id']}/ops/{_operation}", args,
                            connection_id=_connection)["result"]

    def post(self, room_id: str, text: str) -> str | None:
        result = self.request("POST", f"/agents/{self.agent['id']}/message", {"room_id": room_id, "content": text})
        return (result or {}).get("event_id")

    def participants(self, room_id: str) -> list[dict[str, Any]]:
        """[{type: "agent", agent_id, name} | {type: "user", name: <messaging-app username>}]."""
        return self.request("GET", f"/agents/rooms/{room_id}/participants")["participants"]

    def history(self, room_id: str, page: int = 200) -> list[dict[str, Any]]:
        """Every message in a room, oldest first, paging back while Switch says the read was truncated."""
        entries: dict[str, dict[str, Any]] = {}
        before = None
        while True:
            args = {"room_id": room_id, "limit": page, **({"before": before} if before else {})}
            context = self.op("read_context", **args)
            for thread in context.get("threads", []):
                for entry in [thread.get("root"), *(thread.get("replies") or [])]:
                    if entry and entry.get("kind") == "message":
                        entries[entry["id"]] = entry
            oldest = context.get("oldest_timestamp")
            if not context.get("truncated") or oldest is None:
                break
            before = datetime.fromtimestamp(oldest / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return sorted(entries.values(), key=lambda e: e.get("timestamp", 0))

    def rooms(self) -> list[dict[str, Any]]:
        return self.op("list_rooms")

    def ws_url(self, params: dict[str, str]) -> str:
        scheme = "wss" if self.base.startswith("https") else "ws"
        host = self.base.split("://", 1)[1]
        return f"{scheme}://{host}/agents/{self.agent['id']}/connection/ws?{urllib.parse.urlencode(params)}"


def register(name: str, description: str, base: str = DEFAULT_BASE) -> dict[str, Any]:
    if not REGISTRATION_KEY.exists():
        raise SwitchError(f"No registration key at {REGISTRATION_KEY}.")
    body = {
        "name": name, "display_name": name, "description": description, "connector_type": "budget-treaty",
        "integration_profile": {"connection_model": "always_on", "message_exchange": True,
                                "pre_invocation_mediation": [], "post_invocation_mediation": [], "event_reporting": []},
    }
    result = Switch(base).request("POST", "/agents", body, token=REGISTRATION_KEY.read_text().strip())
    agent = {"id": result["id"], "api_key": result["api_key"], "name": name, "base": base,
             "connection_id": str(uuid.uuid4()), "ops_connection_id": str(uuid.uuid4())}
    save_private(AGENT_FILE, agent)
    return agent


async def listen(switch: Switch, on_frame: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    """Hold the main socket: answer pings at once, hand message frames on, reconnect on drops.

    It always starts from the head: anything missed while the keeper was down is recovered from room history.
    """
    agent = switch.agent
    while True:
        params = {"connection_id": agent["connection_id"], "scope": "all", "filter": "all", "start_from": "head",
                  "client": agent["name"], "client_version": "0.1.0"}
        try:
            async with websockets.connect(switch.ws_url(params),
                                          additional_headers={"Authorization": f"Bearer {agent['api_key']}"}) as ws:
                cursor = 0
                async for raw in ws:
                    frame = json.loads(raw)
                    kind, data = frame.get("event"), frame.get("data") or {}
                    if kind == "ping":
                        await ws.send(json.dumps({"type": "pong", "cursor": cursor}))
                    elif kind == "connection_state":
                        cursor = data.get("cursor") or 0
                        print("Connected to Switch.")
                    elif kind == "message":
                        cursor = frame.get("id", cursor)
                        await on_frame(frame)
                    elif kind == "evicted":
                        print(f"Evicted: {data.get('code')} {data.get('reason', '')}")
                        if data.get("code") in ("taken_over", "closed"):
                            return
                        break
                    elif kind == "refused":
                        print(f"Refused: {data.get('status')} {data.get('detail')}")
                        return
        except websockets.ConnectionClosed as closed:
            code = closed.rcvd.code if closed.rcvd else None
            if code in STOP_CLOSE_CODES:
                print(f"Switch closed the connection with {code}; not retrying.")
                return
            print(f"Connection dropped ({code}); reconnecting.")
        except OSError as error:
            print(f"Can't reach Switch ({error}); retrying.")
        await asyncio.sleep(2)


async def hold_room(switch: Switch, room_id: str, connection_id: str, ready: asyncio.Event,
                    on_frame: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    """A second socket that claims one room, so room-scoped ops have a room to act in.

    Claiming takes the room away from the `scope=all` socket ("an `all` connection covers everything no sibling has
    claimed"), so this socket must deliver that room's messages itself: filter `all`, frames passed on.
    """
    agent = switch.agent
    while True:
        params = {"connection_id": connection_id, "scope": "single", "filter": "all", "rooms": room_id,
                  "start_from": "head", "client": f"{agent['name']}-ops"}
        try:
            async with websockets.connect(switch.ws_url(params),
                                          additional_headers={"Authorization": f"Bearer {agent['api_key']}"}) as ws:
                async for raw in ws:
                    frame = json.loads(raw)
                    if frame.get("event") == "ping":
                        await ws.send(json.dumps({"type": "pong", "cursor": 0}))
                    elif frame.get("event") == "connection_state":
                        ready.set()
                    elif frame.get("event") == "message":
                        await on_frame(frame)
                    elif frame.get("event") in ("evicted", "refused"):
                        print(f"Room socket: {frame.get('event')} {frame.get('data')}")
                        break
        except (websockets.ConnectionClosed, OSError) as error:
            print(f"Room socket dropped ({error}); reconnecting.")
        ready.clear()
        await asyncio.sleep(2)
