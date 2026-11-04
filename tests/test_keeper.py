"""The adapter's pure helpers: history replay order and the wall audit. No Switch needed."""

from treaty.core import PRIVATE, TRIP
from treaty.keeper import intruders, merged_history, to_event

KEEPER_ID = "agent-keeper-uuid"


def test_history_from_all_rooms_merges_in_time_order_without_the_keepers_own_posts():
    trip = [{"id": "t1", "sender_name": "bob", "body": "check B2", "timestamp": 30},
            {"id": "t2", "sender_name": "treaty-keeper", "body": "B2: ✅ fits everyone.", "timestamp": 31}]
    alice = [{"id": "a1", "sender_name": "alice", "body": "budget 9000", "timestamp": 10}]
    events = merged_history([(trip, TRIP, None), (alice, PRIVATE, "alice")], own_name="treaty-keeper")
    assert [e["id"] for e in events] == ["a1", "t1"]
    assert events[0] == {"id": "a1", "room": PRIVATE, "owner": "alice", "sender": "alice", "body": "budget 9000",
                         "ts": 10}


def test_live_payload_and_history_entry_map_to_the_same_event():
    payload = {"message_id": "m9", "sender_name": "chen", "body": "in B2", "timestamp": 99}
    entry = {"id": "m9", "sender_name": "chen", "body": "in B2", "timestamp": 99}
    assert to_event(payload, TRIP, None) == to_event(entry, TRIP, None)


def test_only_the_keeper_and_the_friend_may_be_in_a_private_room():
    room = [{"type": "agent", "agent_id": KEEPER_ID, "name": "treaty-keeper"},
            {"type": "user", "agent_id": None, "name": "alice"}]
    assert intruders(room, KEEPER_ID, "alice") == []
    planted = room + [{"type": "agent", "agent_id": "planner-uuid", "name": "treaty-planner"}]
    assert intruders(planted, KEEPER_ID, "alice") == ["agent treaty-planner"]
    nosy = room + [{"type": "user", "agent_id": None, "name": "bob"}]
    assert intruders(nosy, KEEPER_ID, "alice") == ["person bob"]
