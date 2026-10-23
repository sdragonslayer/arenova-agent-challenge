"""Connect Four agent: opening book -> exact (weak) solve -> heuristic search."""
import os

from common.io import log
from common.timer import OutOfTime
from connect4.board import canonical, legal_columns, play_columns, wins_now
from connect4.heuristic import Heuristic
from connect4.solver import ORDER, Solver, move_bit, winning_positions, possible

BOOK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "book.txt")


def load_book():
    book = {}
    try:
        with open(BOOK_PATH) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    book[int(parts[0], 16)] = int(parts[1])
    except OSError:
        pass
    return book


class Agent:
    def __init__(self, seat):
        self.seat = seat
        self.book = load_book()
        self.solver = Solver()
        self.heur = Heuristic()
        self.cache = {}  # canonical key -> chosen column (for repeated requests)
        log("c4 book entries", len(self.book))

    # -- state -------------------------------------------------------------
    @staticmethod
    def columns_from_history(history):
        drops = []
        for item in sorted(history, key=lambda it: it.get("seq", 0)):
            ev = item.get("event") or {}
            if ev.get("type") == "disc-dropped":
                drops.append(int(ev["column"]))
        return drops

    def position(self, msg):
        cols = self.columns_from_history(msg.get("history") or [])
        return play_columns(cols)

    # -- decisions ---------------------------------------------------------
    def fallback(self, msg):
        cur, mask, moves = self.position(msg)
        legal = legal_columns(mask)
        for c in legal:
            if wins_now(cur, mask, c):
                return {"type": "disc-dropped", "column": c}
        opp = cur ^ mask
        threats = winning_positions(opp, mask) & possible(mask)
        for c in legal:
            if threats & move_bit(mask, c):
                return {"type": "disc-dropped", "column": c}
        safe = [c for c in legal if not (winning_positions(opp, mask) & (move_bit(mask, c) << 1))]
        pool = safe or legal
        c = min(pool, key=ORDER.index)
        return {"type": "disc-dropped", "column": c}

    def decide(self, msg, budget):
        cur, mask, moves = self.position(msg)
        col = self.choose(cur, mask, moves, budget)
        return {"type": "disc-dropped", "column": int(col)}

    def choose(self, cur, mask, moves, budget):
        legal = legal_columns(mask)
        if len(legal) == 1:
            return legal[0]
        for c in legal:
            if wins_now(cur, mask, c):
                return c

        k, mir = canonical(cur, mask)
        if k in self.book:
            c = self.book[k]
            c = 6 - c if mir else c
            if c in legal:
                return c

        if len(self.solver.tt) > 1_200_000:
            self.solver.tt.clear()

        # Exact weak solve, child by child, in heuristic order. Stop at the first win.
        order = self.ordered(cur, mask, legal)
        results = {}
        solve_budget = budget.sub(budget.remaining() * 0.8)
        try:
            for c in order:
                m = move_bit(mask, c)
                ncur, nmask = cur ^ mask, mask | m
                if winning_positions(ncur, nmask) & possible(nmask):
                    results[c] = -1
                    continue
                self.solver.budget = solve_budget
                r = -self.solver.solve(ncur, nmask, moves + 1, weak=True)
                results[c] = r
                if r > 0:
                    return c
        except OutOfTime:
            pass
        finally:
            self.solver.budget = None

        draws = [c for c in order if results.get(c) == 0]
        unknown = [c for c in order if c not in results]
        if not unknown and draws:
            return draws[0] if len(draws) == 1 else self.heur.best_move(cur, mask, moves, draws, budget)
        # Prefer unresolved moves (could be wins) and draws over proven losses.
        pool = unknown + draws
        if not pool:
            pool = [c for c in order if results.get(c, -1) > -1] or order
            # All moves lose: pick the one the heuristic likes best (most resistance).
        return self.heur.best_move(cur, mask, moves, pool, budget)

    def ordered(self, cur, mask, legal):
        from connect4.solver import popcount
        opp = cur ^ mask
        opp_win = winning_positions(opp, mask)

        def key(c):
            m = move_bit(mask, c)
            loses = bool(opp_win & (m << 1))
            return (loses, -popcount(winning_positions(cur | m, mask)), ORDER.index(c))

        return sorted(legal, key=key)
