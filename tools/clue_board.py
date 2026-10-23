"""A classic-like 24x25 Clue board in the runner's JSON shape (for local testing only).

The real board arrives in game-started; replace this with a captured payload
(tools/fixtures/clue_game_started.json) when available.
"""
import json
import os

ROOMS = {
    # id: (row0, row1, col0, col1) inclusive footprint
    "study": (0, 3, 0, 6),
    "hall": (0, 6, 9, 14),
    "lounge": (0, 5, 17, 23),
    "library": (6, 10, 0, 6),
    "billiard-room": (12, 16, 0, 5),
    "dining-room": (9, 15, 16, 23),
    "conservatory": (19, 24, 0, 5),
    "ballroom": (17, 23, 8, 15),
    "kitchen": (18, 24, 18, 23),
}
CELLAR = (8, 14, 9, 13)
DOORS = [
    ("study", (4, 6)),
    ("hall", (7, 11)), ("hall", (7, 12)), ("hall", (4, 8)),
    ("lounge", (6, 17)),
    ("library", (8, 7)), ("library", (11, 3)),
    ("billiard-room", (11, 1)), ("billiard-room", (15, 6)),
    ("dining-room", (8, 17)), ("dining-room", (12, 15)),
    ("conservatory", (18, 4)),
    ("ballroom", (16, 9)), ("ballroom", (16, 14)), ("ballroom", (19, 7)), ("ballroom", (19, 16)),
    ("kitchen", (17, 19)),
]
STARTS = {
    "scarlet": (0, 16),
    "mustard": (7, 23),
    "white": (24, 16),
    "green": (24, 7),
    "peacock": (18, 0),
    "plum": (5, 0),
}
LABELS = {
    "study": "Study", "hall": "Hall", "lounge": "Lounge", "library": "Library",
    "billiard-room": "Billiard Room", "dining-room": "Dining Room",
    "conservatory": "Conservatory", "ballroom": "Ballroom", "kitchen": "Kitchen",
}


def build():
    W, H = 24, 25
    grid = [["-"] * W for _ in range(H)]
    for rid, (r0, r1, c0, c1) in ROOMS.items():
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                grid[r][c] = "R"
    r0, r1, c0, c1 = CELLAR
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            grid[r][c] = "X"
    # A few wall notches to make corridors less open.
    for r, c in [(0, 7), (0, 8), (0, 15), (24, 6), (24, 17), (11, 0), (17, 0), (6, 23), (16, 23)]:
        grid[r][c] = "#"
    for _, (r, c) in DOORS:
        assert grid[r][c] == "-", (r, c)
    rooms = []
    for rid, (r0, r1, c0, c1) in ROOMS.items():
        rooms.append({
            "id": rid, "label": LABELS[rid],
            "footprint": {"row": r0, "column": c0, "height": r1 - r0 + 1, "width": c1 - c0 + 1},
            "doors": [{"row": r, "column": c} for room, (r, c) in DOORS if room == rid],
        })
    return {
        "width": W, "height": H,
        "rows": ["".join(row) for row in grid],
        "cells": [],
        "rooms": rooms,
        "cellar": {"row": CELLAR[0], "column": CELLAR[2]},
        "doors": [{"room": room, "corridor": {"row": r, "column": c}} for room, (r, c) in DOORS],
        "starts": {k: {"row": r, "column": c} for k, (r, c) in STARTS.items()},
        "secretPassages": [{"from": "study", "to": "kitchen"}, {"from": "lounge", "to": "conservatory"}],
    }


def load():
    fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "clue_game_started.json")
    if os.path.exists(fixture):
        with open(fixture) as f:
            return json.load(f)["board"]
    return build()


if __name__ == "__main__":
    b = build()
    print("\n".join(b["rows"]))
