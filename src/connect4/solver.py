"""Connect Four bitboard solver (after Pascal Pons' approach).

Bit layout: column c occupies bits c*7 .. c*7+6; bit c*7+h is height h
(h=0 bottom). The 7th bit of each column is a sentinel that stays empty.
A position is (cur, mask): cur = stones of the side to move, mask = all stones.
Scores follow Pons: positive = side to move wins, larger = sooner;
0 = draw; negative = loses.
"""
from common.timer import OutOfTime

W, H = 7, 6
H1 = H + 1
SIZE = W * H
BOTTOM = sum(1 << (c * H1) for c in range(W))
BOARD = BOTTOM * ((1 << H) - 1)
COL_MASK = [((1 << H) - 1) << (c * H1) for c in range(W)]
TOP_MASK = [(1 << (H - 1)) << (c * H1) for c in range(W)]
BOTTOM_MASK = [1 << (c * H1) for c in range(W)]
ORDER = [3, 2, 4, 1, 5, 0, 6]
MIN_SCORE = -(SIZE // 2) + 3


def popcount(x):
    return bin(x).count("1")


def winning_positions(pos, mask):
    """Empty cells where `pos` would complete four."""
    r = (pos << 1) & (pos << 2) & (pos << 3)
    for s in (H1, H, H + 2):
        p = (pos << s) & (pos << (2 * s))
        r |= p & (pos << (3 * s))
        r |= p & (pos >> s)
        p = (pos >> s) & (pos >> (2 * s))
        r |= p & (pos << s)
        r |= p & (pos >> (3 * s))
    return r & (BOARD ^ mask)


def is_win(pos):
    for s in (1, H1, H, H + 2):
        m = pos & (pos >> s)
        if m & (m >> (2 * s)):
            return True
    return False


def possible(mask):
    return (mask + BOTTOM) & BOARD


def can_play(mask, c):
    return (mask & TOP_MASK[c]) == 0


def move_bit(mask, c):
    return (mask + BOTTOM_MASK[c]) & COL_MASK[c]


def column_of(bit):
    return (bit.bit_length() - 1) // H1


class Solver:
    def __init__(self):
        self.tt = {}
        self.nodes = 0
        self.budget = None

    def non_losing_moves(self, cur, mask):
        poss = possible(mask)
        opp_win = winning_positions(cur ^ mask, mask)
        forced = poss & opp_win
        if forced:
            if forced & (forced - 1):
                return 0
            poss = forced
        return poss & ~(opp_win >> 1)

    def negamax(self, cur, mask, moves, alpha, beta):
        # Precondition: side to move cannot win immediately.
        self.nodes += 1
        if (self.nodes & 2047) == 0 and self.budget is not None:
            self.budget.check()
        nxt = self.non_losing_moves(cur, mask)
        if nxt == 0:
            return -((SIZE - moves) // 2)
        if moves >= SIZE - 2:
            return 0
        lo = -((SIZE - 2 - moves) // 2)
        if alpha < lo:
            alpha = lo
            if alpha >= beta:
                return alpha
        hi = (SIZE - 1 - moves) // 2
        key = cur + mask
        v = self.tt.get(key)
        if v is not None:
            hi = v + MIN_SCORE - 1
        if beta > hi:
            beta = hi
            if alpha >= beta:
                return beta

        # Order moves: most new threats first, ties broken by centrality.
        cand = []
        for i, c in enumerate(ORDER):
            m = nxt & COL_MASK[c]
            if m:
                cand.append((-popcount(winning_positions(cur | m, mask)), i, m))
        cand.sort()
        opp = cur ^ mask
        for _, _, m in cand:
            score = -self.negamax(opp, mask | m, moves + 1, -beta, -alpha)
            if score >= beta:
                return score
            if score > alpha:
                alpha = score
        self.tt[key] = alpha - MIN_SCORE + 1
        if len(self.tt) > 4_000_000:
            self.tt.clear()
        return alpha

    def solve(self, cur, mask, moves, weak=False):
        if winning_positions(cur, mask) & possible(mask):
            return (SIZE + 1 - moves) // 2
        lo = -((SIZE - moves) // 2)
        hi = (SIZE + 1 - moves) // 2
        if weak:
            lo, hi = -1, 1
        while lo < hi:
            med = lo + (hi - lo) // 2
            if med <= 0 and int(lo / 2) < med:
                med = int(lo / 2)
            elif med >= 0 and int(hi / 2) > med:
                med = int(hi / 2)
            r = self.negamax(cur, mask, moves, med, med + 1)
            if r <= med:
                hi = r
            else:
                lo = r
        return lo

    def move_scores(self, cur, mask, moves, weak=False, budget=None):
        """Exact score of every legal column (from the mover's view).

        Raises OutOfTime if the budget runs out.
        """
        self.budget = budget
        try:
            scores = {}
            win_now = winning_positions(cur, mask) & possible(mask)
            for c in ORDER:
                if not can_play(mask, c):
                    continue
                m = move_bit(mask, c)
                if win_now & m:
                    scores[c] = (SIZE + 1 - moves) // 2
                    continue
                if win_now:
                    continue  # a winning move exists; don't bother with others
                ncur, nmask = cur ^ mask, mask | m
                if winning_positions(ncur, nmask) & possible(nmask):
                    scores[c] = -((SIZE - moves) // 2)  # opponent wins next move
                    continue
                if nmask == BOARD:
                    scores[c] = 0
                    continue
                scores[c] = -self.solve(ncur, nmask, moves + 1, weak=weak)
            return scores
        finally:
            self.budget = None
