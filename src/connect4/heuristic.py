"""Depth-limited alpha-beta with a threat-based evaluation (used when exact solving runs out of time)."""
from common.timer import OutOfTime
from connect4.solver import (
    BOARD, COL_MASK, H1, ORDER, SIZE, popcount, possible, winning_positions,
)

# Bit masks for "odd" rows counted from the bottom (h = 0, 2, 4 -> rows 1, 3, 5).
ODD_ROWS = sum(1 << (c * H1 + h) for c in range(7) for h in (0, 2, 4))
EVEN_ROWS = sum(1 << (c * H1 + h) for c in range(7) for h in (1, 3, 5))
CENTER_WEIGHT = [1, 2, 3, 4, 3, 2, 1]
CENTER_MASKS = [(COL_MASK[c], CENTER_WEIGHT[c]) for c in range(7)]
WIN = 10_000


def evaluate(cur, mask, moves):
    """Score from the side-to-move's perspective."""
    opp = cur ^ mask
    first_to_move = (moves % 2 == 0)  # side to move is the first player
    mine = winning_positions(cur, mask)
    theirs = winning_positions(opp, mask)
    my_good = ODD_ROWS if first_to_move else EVEN_ROWS
    their_good = EVEN_ROWS if first_to_move else ODD_ROWS
    score = 0
    score += 6 * popcount(mine & my_good) + 3 * popcount(mine & ~my_good)
    score -= 6 * popcount(theirs & their_good) + 3 * popcount(theirs & ~their_good)
    # Stacked threats (two threats directly above each other) are usually decisive.
    score += 8 * popcount(mine & (mine >> 1))
    score -= 8 * popcount(theirs & (theirs >> 1))
    for cm, w in CENTER_MASKS:
        score += w * (popcount(cur & cm) - popcount(opp & cm))
    return score


class Heuristic:
    def __init__(self):
        self.nodes = 0
        self.budget = None

    def search(self, cur, mask, moves, depth, alpha, beta):
        self.nodes += 1
        if (self.nodes & 1023) == 0 and self.budget is not None:
            self.budget.check()
        poss = possible(mask)
        if winning_positions(cur, mask) & poss:
            return WIN - moves
        if mask == BOARD or moves >= SIZE:
            return 0
        opp = cur ^ mask
        opp_win = winning_positions(opp, mask)
        forced = poss & opp_win
        if forced:
            if forced & (forced - 1):
                return -(WIN - moves - 1)
            poss = forced
        safe = poss & ~(opp_win >> 1)
        if not safe:
            return -(WIN - moves - 2)
        if depth <= 0:
            return evaluate(cur, mask, moves)
        cand = []
        for i, c in enumerate(ORDER):
            m = safe & COL_MASK[c]
            if m:
                cand.append((-popcount(winning_positions(cur | m, mask)), i, m))
        cand.sort()
        best = -WIN * 2
        for _, _, m in cand:
            v = -self.search(opp, mask | m, moves + 1, depth - 1, -beta, -alpha)
            if v > best:
                best = v
            if v > alpha:
                alpha = v
            if alpha >= beta:
                break
        return best

    def best_move(self, cur, mask, moves, columns, budget, max_depth=14):
        """Iterative deepening over the given candidate columns."""
        from connect4.solver import move_bit
        self.budget = budget
        best_col = columns[0]
        try:
            for depth in range(2, max_depth + 1):
                scores = {}
                alpha = -WIN * 2
                for c in sorted(columns, key=lambda c: (c != best_col, ORDER.index(c))):
                    m = move_bit(mask, c)
                    if winning_positions(cur, mask) & m:
                        return c
                    v = -self.search(cur ^ mask, mask | m, moves + 1, depth - 1, -WIN * 2, -alpha)
                    scores[c] = v
                    if v > alpha:
                        alpha = v
                best_col = max(columns, key=lambda c: (scores[c], -ORDER.index(c)))
                if abs(scores[best_col]) > WIN // 2:
                    break
        except OutOfTime:
            pass
        finally:
            self.budget = None
        return best_col
