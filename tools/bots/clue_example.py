"""Arenova's example Clue agent (from the docs), used as a baseline opponent."""
import json
import sys

seat = None             # This state lives for the whole match.
requests_handled = 0

def events(history):
    return [item["event"] for item in history]

def snapshot(history):
    all_events = events(history)
    start = next(event for event in all_events if event["type"] == "game-started")
    positions = {
        suspect: dict(position)
        for suspect, position in start["suspectPositions"].items()
    }
    roll = start["roll"]
    may_suggest_without_moving = False
    for event in all_events:
        if event["type"] == "pawn-moved":
            positions[event["suspect"]] = dict(event["to"])
            if event["seat"] == start["you"]["seat"]:
                may_suggest_without_moving = False
        if event["type"] == "suggestion-made":
            positions[event["suggestion"]["suspect"]] = {
                "kind": "room",
                "room": event["suggestion"]["room"],
            }
            if event["suggester"] == start["you"]["seat"]:
                may_suggest_without_moving = False
            if event.get("pulledSeat") == start["you"]["seat"]:
                may_suggest_without_moving = True
        if event["type"] == "accusation-made":
            if event["accuser"] == start["you"]["seat"]:
                may_suggest_without_moving = False
            if event.get("unblockedInto"):
                positions[start["characters"][event["accuser"]]] = {
                    "kind": "room",
                    "room": event["unblockedInto"],
                }
        if event["type"] == "turn-ended" and event["seat"] == start["you"]["seat"]:
            may_suggest_without_moving = False
        if event["type"] in ("turn-ended", "accusation-made") and event.get("nextRoll"):
            roll = event["nextRoll"]
    return start, positions, roll, may_suggest_without_moving

def make_suggestion(start, room):
    hand = set(start["hand"])
    suspect = next(
        (value for value in start["suspects"] if "suspect:" + value not in hand),
        start["suspects"][0],
    )
    weapon = next(
        (value for value in start["weapons"] if "weapon:" + value not in hand),
        start["weapons"][0],
    )
    return {"type": "suggestion-made", "suspect": suspect, "weapon": weapon, "room": room}

def move(history):
    start, positions, roll, may_suggest_without_moving = snapshot(history)
    character = start["you"]["character"]
    origin = positions[character]
    board = start["board"]
    distance = sum(roll)
    key = lambda cell: f'{cell["row"]},{cell["column"]}'
    starts = {key(cell) for cell in board["starts"].values()}
    occupied = {
        key(position)
        for suspect, position in positions.items()
        if suspect != character and position["kind"] == "corridor"
    }

    def walk(at, path, visited):
        door = next(
            (
                candidate for candidate in board["doors"]
                if candidate["corridor"]["row"] == at["row"]
                and candidate["corridor"]["column"] == at["column"]
            ),
            None,
        )
        if door:
            return {"type": "pawn-moved", "path": path, "enterRoom": door["room"]}
        if len(path) == distance:
            return {"type": "pawn-moved", "path": path}

        neighbors = [
            {"row": at["row"] - 1, "column": at["column"]},
            {"row": at["row"], "column": at["column"] - 1},
            {"row": at["row"], "column": at["column"] + 1},
            {"row": at["row"] + 1, "column": at["column"]},
        ]
        for nxt in neighbors:
            nxt_key = key(nxt)
            if (
                nxt["row"] < 0 or nxt["row"] >= board["height"]
                or nxt["column"] < 0 or nxt["column"] >= board["width"]
                or board["rows"][nxt["row"]][nxt["column"]] != "-"
                or nxt_key in starts or nxt_key in occupied or nxt_key in visited
            ):
                continue
            found = walk(nxt, path + [nxt], visited | {nxt_key})
            if found:
                return found
        return None

    if origin["kind"] == "room":
        if may_suggest_without_moving:
            return make_suggestion(start, origin["room"])
        passage = next(
            (
                candidate for candidate in board["secretPassages"]
                if candidate["from"] == origin["room"] or candidate["to"] == origin["room"]
            ),
            None,
        )
        if passage:
            enter_room = passage["to"] if passage["from"] == origin["room"] else passage["from"]
            return {
                "type": "pawn-moved",
                "path": [],
                "enterRoom": enter_room,
                "useSecretPassage": True,
            }
        for door in (candidate for candidate in board["doors"] if candidate["room"] == origin["room"]):
            door_key = key(door["corridor"])
            if door_key in starts or door_key in occupied:
                continue
            found = walk(door["corridor"], [dict(door["corridor"])], {door_key})
            if found:
                return found
        return {"type": "turn-ended"}
    return walk(origin, [], {key(origin)}) or {"type": "turn-ended"}

def decide(history, request):
    start, positions, _roll, _may_suggest = snapshot(history)
    if request["kind"] == "take-turn":
        return move(history)
    if request["kind"] == "make-suggestion":
        return make_suggestion(start, positions[start["you"]["character"]]["room"])
    if request["kind"] == "show-card":
        current = next(
            event for event in reversed(events(history))
            if event["type"] == "suggestion-made" and event["refuter"] == start["you"]["seat"]
        )
        cards = [
            "suspect:" + current["suggestion"]["suspect"],
            "weapon:" + current["suggestion"]["weapon"],
            "room:" + current["suggestion"]["room"],
        ]
        return {"type": "card-shown", "card": next(card for card in cards if card in start["hand"])}
    if request["kind"] == "accuse-or-end":
        return {"type": "turn-ended"}
    raise ValueError("unexpected request")

def handle(message):
    global seat, requests_handled
    if message["type"] == "init":
        seat = message["seat"]
        return {}
    if message["type"] != "decision":
        raise ValueError("unknown request type")
    requests_handled += 1
    return decide(message["history"], message["decision"])

for line in sys.stdin:
    try:
        print(json.dumps(handle(json.loads(line)), separators=(",", ":")), flush=True)
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        raise
