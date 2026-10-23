"""Local Connect Four engine.

Usage: python tools/sim_c4.py -n 20 --red proc --yellow random
Specs: ours (in-process), proc (src/main.py subprocess), random, greedy.
"""
import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proc import make_bot, InProcBot  # noqa: E402
from connect4.board import play_columns, legal_columns, wins_now  # noqa: E402
from connect4.solver import is_win, BOARD  # noqa: E402


class RandomBot:
    def __init__(self, seat, greedy=False):
        self.greedy = greedy
        self.rng = random.Random()

    def decide(self, msg, budget=None):
        cols = [it["event"]["column"] for it in msg["history"] if it["event"]["type"] == "disc-dropped"]
        cur, mask, n = play_columns(cols)
        legal = legal_columns(mask)
        if self.greedy:
            for c in legal:
                if wins_now(cur, mask, c):
                    return {"type": "disc-dropped", "column": c}
            opp = cur ^ mask
            for c in legal:
                if wins_now(opp, mask, c):
                    return {"type": "disc-dropped", "column": c}
        return {"type": "disc-dropped", "column": self.rng.choice(legal)}

    fallback = decide


def bot(spec, seat):
    if spec == "random":
        return InProcBot(lambda s: RandomBot(s), seat, name="random")
    if spec == "greedy":
        return InProcBot(lambda s: RandomBot(s, greedy=True), seat, name="greedy")
    return make_bot(spec, seat, lambda: __import__("connect4.agent", fromlist=["Agent"]).Agent, soft=6.0)


def play(red, yellow, deadline=65.0):
    bots = {"seat-1": bot(red, "seat-1"), "seat-2": bot(yellow, "seat-2")}
    for s, b in bots.items():
        b.request({"type": "init", "game": "connect-four", "seat": s}, timeout=15)
    hist = []
    start = {"type": "game-started", "red": "seat-1", "yellow": "seat-2", "columns": 7, "rows": 6}
    cols = []
    times = {"seat-1": 0.0, "seat-2": 0.0}
    result = None
    for ply in range(42):
        seat = "seat-1" if ply % 2 == 0 else "seat-2"
        you = 1 if seat == "seat-1" else 2
        h = [{"seq": 0, "atMs": 0, "event": dict(start, you=you)}] + hist
        msg = {"type": "decision", "decision": {"requestId": "turn:%d" % (ply + 1), "seat": seat, "kind": "disc-dropped"},
               "history": h, "deadlineMs": int(deadline * 1000)}
        r, dt = bots[seat].request(msg, timeout=deadline)
        times[seat] += dt
        cur, mask, _ = play_columns(cols)
        c = (r or {}).get("column")
        if not isinstance(c, int) or c not in legal_columns(mask) or r.get("type") != "disc-dropped":
            result = ("forfeit", seat, r)
            break
        row = 5 - bin((mask >> (c * 7)) & 0x3f).count("1")
        cols.append(c)
        hist.append({"seq": len(hist) + 1, "atMs": ply, "event": {"type": "disc-dropped", "seat": seat, "column": c, "row": row, "player": you}})
        cur2, mask2, _ = play_columns(cols)
        mover = cur2 ^ mask2  # stones of the player who just moved
        if is_win(mover):
            result = ("win", seat, None)
            break
        if mask2 == BOARD:
            result = ("draw", None, None)
            break
    for b in bots.values():
        b.close()
    return result, times, "".join(str(c + 1) for c in cols)


def main():
    from lowprio import set_low_priority
    set_low_priority()
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--red", default="ours")
    ap.add_argument("--yellow", default="random")
    args = ap.parse_args()
    tally = {}
    for g in range(args.n):
        t0 = time.time()
        (kind, seat, extra), times, moves = play(args.red, args.yellow)
        key = kind if kind == "draw" else "%s %s" % (seat, kind)
        tally[key] = tally.get(key, 0) + 1
        print("game %d: %s %s moves=%s red=%.1fs yellow=%.1fs %s" % (
            g, kind, seat, moves, times["seat-1"], times["seat-2"], extra or ""), flush=True)
    print(tally)


if __name__ == "__main__":
    main()
