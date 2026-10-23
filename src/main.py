"""Crucible entrypoint: one long-running process per seat.

Protocol: one JSON request per stdin line, one JSON reply per stdout line.
Every decision runs in a worker thread under a watchdog; if the game logic
raises or runs long, a cheap legal fallback move is sent instead, because an
invalid or late reply is a fault (forfeit / elimination / aborted match).
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common.io import log, write_reply  # noqa: E402
from common.timer import Budget  # noqa: E402

# Fraction of deadlineMs we allow ourselves, and an absolute cap per game.
SOFT_FRACTION = 0.70
HARD_FRACTION = 0.85
SAFETY_SECONDS = 3.0
GAME_CAPS = {"connect-four": 7.0, "clue": 25.0, "secret-dictator": 40.0}


def load_agent(game, seat):
    if game == "connect-four":
        from connect4.agent import Agent
    elif game == "clue":
        from clue.agent import Agent
    elif game == "secret-dictator":
        from sd.agent import Agent
    else:
        raise ValueError("unknown game %r" % game)
    return Agent(seat)


class Runner:
    def __init__(self):
        self.agent = None
        self.game = None
        self.seat = None
        self.worker = None

    def handle_init(self, msg):
        self.game = msg.get("game")
        self.seat = msg.get("seat")
        try:
            self.agent = load_agent(self.game, self.seat)
        except Exception as e:
            log("init failed", repr(e))
            self.agent = None
        return {}

    def handle_decision(self, msg):
        if self.agent is None:
            # Late init (or init failed): try once more from the decision itself.
            self.game = self.game or _guess_game(msg)
            self.seat = self.seat or (msg.get("decision") or {}).get("seat")
            self.agent = load_agent(self.game, self.seat)

        # Let any straggling worker from the previous request wind down first.
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=2.0)

        deadline = float(msg.get("deadlineMs") or 15000) / 1000.0
        cap = GAME_CAPS.get(self.game, 10.0)
        soft = max(0.2, min(cap, deadline * SOFT_FRACTION - SAFETY_SECONDS))
        hard = max(0.5, min(cap + 5.0, deadline * HARD_FRACTION - SAFETY_SECONDS))
        budget = Budget(soft)

        box = {}

        def work():
            try:
                box["reply"] = self.agent.decide(msg, budget)
            except Exception as e:
                import traceback
                box["error"] = e
                log("decide error", repr(e), traceback.format_exc()[-1500:])

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.worker.join(timeout=hard)
        reply = box.get("reply")
        if reply is None:
            if self.worker.is_alive():
                log("decide timed out; using fallback", (msg.get("decision") or {}).get("kind"))
            try:
                reply = self.agent.fallback(msg)
            except Exception as e:
                log("fallback error", repr(e))
                reply = {}
        return reply


def _guess_game(msg):
    kind = (msg.get("decision") or {}).get("kind", "")
    if kind == "disc-dropped":
        return "connect-four"
    if kind in ("take-turn", "make-suggestion", "show-card", "accuse-or-end"):
        return "clue"
    return "secret-dictator"


def main():
    runner = Runner()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        t0 = time.monotonic()
        try:
            msg = json.loads(line)
        except Exception as e:
            log("bad json from runner", repr(e))
            continue
        try:
            if msg.get("type") == "init":
                reply = runner.handle_init(msg)
            elif msg.get("type") == "decision":
                reply = runner.handle_decision(msg)
            else:
                reply = {}
        except Exception as e:
            log("handler error", repr(e))
            reply = {}
        write_reply(reply)
        if msg.get("type") == "decision":
            log("t=%.2fs" % (time.monotonic() - t0), (msg.get("decision") or {}).get("requestId"), json.dumps(reply)[:300], low_priority=True)


if __name__ == "__main__":
    main()
