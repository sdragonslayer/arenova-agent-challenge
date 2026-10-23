"""Offline Connect Four opening-book generator (red = first player).

Proves the first-player win through ply LEAF with a guessed move at every red
node and all yellow replies; leaves (red to move at ply LEAF) are solved by
worker processes, which also record a winning move for the leaf. Misguesses are
found when a leaf fails, and alternatives are tried in later rounds.

Output: src/connect4/book.txt, lines "<canonical key hex> <column>".
Usage: python tools/c4_book.py [LEAF] [WORKERS]   (defaults: 10, 2 low-priority workers)
Resumable: solved leaves are cached in tools/c4_leaf_cache.json; Ctrl+C anytime.
"""
import json
import multiprocessing as mp
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from connect4.solver import Solver, ORDER, popcount, winning_positions, possible, move_bit, BOARD  # noqa
from connect4.board import canonical, mirror, legal_columns, wins_now, play  # noqa
sys.path.insert(0, os.path.join(ROOT, "tools"))
from lowprio import set_low_priority  # noqa

CACHE = os.path.join(ROOT, "tools", "c4_leaf_cache.json")
OUT = os.path.join(ROOT, "src", "connect4", "book.txt")

_solver = None


def leaf_task(args):
    """Return (key, winning column or -1) for red-to-move position."""
    global _solver
    if _solver is None:
        _solver = Solver()
    k, cur, mask, ply = args
    s = _solver
    if len(s.tt) > 3_000_000:
        s.tt.clear()
    for c in candidates(s, cur, mask):
        if wins_now(cur, mask, c):
            return k, c
        ncur, nmask = play(cur, mask, c)
        if winning_positions(ncur, nmask) & possible(nmask):
            continue
        if nmask == BOARD:
            continue
        if s.solve(ncur, nmask, ply + 1, weak=True) < 0:
            return k, c
    return k, -1


def candidates(s, cur, mask):
    nxt = s.non_losing_moves(cur, mask)
    win = winning_positions(cur, mask) & possible(mask)
    out = []
    for i, c in enumerate(ORDER):
        m = move_bit(mask, c)
        if mask & (1 << (c * 7 + 5)):
            continue
        if win & m:
            return [c]
        if nxt & m:
            out.append((-popcount(winning_positions(cur | m, mask)), i, c))
    out.sort()
    return [c for _, _, c in out]


class Prover:
    def __init__(self, leaf_ply, leaf_results):
        self.leaf_ply = leaf_ply
        self.leaf = leaf_results  # canonical key -> column (-1 = not won)
        self.need = {}
        self.book = {}
        self.memo = {}
        self.s = Solver()

    def prove(self, cur, mask, ply):
        k, mir = canonical(cur, mask)
        if k in self.memo:
            return self.memo[k]
        if ply >= self.leaf_ply:
            if k in self.leaf:
                ok = self.leaf[k] >= 0
            else:
                ccur, cmask = (mirror(cur), mirror(mask)) if mir else (cur, mask)
                self.need[k] = (k, ccur, cmask, ply)
                ok = True  # optimistic until solved
            self.memo[k] = ok
            return ok
        for c in candidates(self.s, cur, mask):
            if wins_now(cur, mask, c):
                self.book[k] = 6 - c if mir else c
                self.memo[k] = True
                return True
            ccur, cmask = play(cur, mask, c)
            if cmask == BOARD:
                continue
            ok = True
            for yc in legal_columns(cmask):
                if wins_now(ccur, cmask, yc):
                    ok = False
                    break
                gcur, gmask = play(ccur, cmask, yc)
                if gmask == BOARD or not self.prove(gcur, gmask, ply + 2):
                    ok = False
                    break
            if ok:
                self.book[k] = 6 - c if mir else c
                self.memo[k] = True
                return True
        self.memo[k] = False
        return False


def main():
    leaf_ply = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    set_low_priority()
    leaf = {}
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            leaf = {int(k): v for k, v in json.load(f).items()}
    round_no = 0
    with mp.Pool(workers, initializer=set_low_priority) as pool:
        while True:
            round_no += 1
            p = Prover(leaf_ply, leaf)
            ok = p.prove(0, 0, 0)
            print("round %d: root=%s book=%d leaves known=%d need=%d" % (
                round_no, ok, len(p.book), len(leaf), len(p.need)), flush=True)
            if not p.need:
                break
            t0 = time.time()
            done = 0
            for k, c in pool.imap_unordered(leaf_task, list(p.need.values()), chunksize=1):
                leaf[k] = c
                done += 1
                if done % 20 == 0 or done == len(p.need):
                    print("  solved %d/%d (%.0fs)" % (done, len(p.need), time.time() - t0), flush=True)
                    with open(CACHE, "w") as f:
                        json.dump({str(a): b for a, b in leaf.items()}, f)
    # Book = proven red nodes + winning moves found at leaves.
    entries = dict(p.book)
    for k, c in leaf.items():
        if c >= 0 and p.memo.get(k):
            entries.setdefault(k, c)
    with open(OUT, "w") as f:
        for k in sorted(entries):
            f.write("%x %d\n" % (k, entries[k]))
    print("wrote %d entries to %s" % (len(entries), OUT))


if __name__ == "__main__":
    main()
