"""Board helpers shared by the agent and the offline book generator."""
from connect4.solver import H1, W, H, move_bit, can_play, winning_positions, possible


def play_columns(cols):
    """Return (cur, mask, moves) after playing the given 0-based columns."""
    cur = mask = 0
    for c in cols:
        m = move_bit(mask, c)
        cur ^= mask
        mask |= m
    return cur, mask, len(cols)


def play(cur, mask, c):
    m = move_bit(mask, c)
    return cur ^ mask, mask | m


def mirror(bb):
    out = 0
    for c in range(W):
        col = (bb >> (c * H1)) & ((1 << H1) - 1)
        out |= col << ((W - 1 - c) * H1)
    return out


def key(cur, mask):
    return cur + mask


def canonical(cur, mask):
    """(canonical key, mirrored?) using left-right symmetry."""
    k = cur + mask
    mk = mirror(cur) + mirror(mask)
    if mk < k:
        return mk, True
    return k, False


def legal_columns(mask):
    return [c for c in range(W) if can_play(mask, c)]


def wins_now(cur, mask, c):
    return bool(winning_positions(cur, mask) & possible(mask) & move_bit(mask, c))
