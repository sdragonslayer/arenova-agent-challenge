"""Card-ownership reasoning for Clue.

Owners: player indices 0..n-1, then three solution slots (suspect, weapon, room),
each with capacity 1 and only able to hold a card of its own category.
Constraints:
  * domains        - which owners a card may still belong to
  * capacities     - exact hand sizes (1 per solution slot)
  * clauses        - "owner o holds at least one of cards S" (refutations we could not see)
  * not_solution   - "these three cards are not all in the solution" (failed accusations)

`feasible` is an exact backtracking check; `Sampler` draws (approximately) uniform
consistent deals with a swap-move Markov chain, which is what the probabilities
and the information-gain estimates are built on.
"""
import random

CATS = ("suspect", "weapon", "room")


def bits(x):
    while x:
        low = x & -x
        yield low.bit_length() - 1
        x ^= low


def popcount(x):
    return bin(x).count("1")


class Knowledge:
    def __init__(self, cards, categories, n_players, hand_sizes):
        self.cards = list(cards)                 # card ids, e.g. "weapon:rope"
        self.index = {c: i for i, c in enumerate(self.cards)}
        self.cat = [CATS.index(categories[c]) for c in self.cards]
        self.n = n_players
        self.n_owners = n_players + 3
        self.capacity = list(hand_sizes) + [1, 1, 1]
        all_players = (1 << n_players) - 1
        self.domain = [all_players | (1 << (n_players + self.cat[i])) for i in range(len(self.cards))]
        self.clauses = []        # (owner, cardmask)
        self.not_solution = []   # cardmask of 3 cards
        self.version = 0

    # -- facts -------------------------------------------------------------
    def card_mask(self, cards):
        m = 0
        for c in cards:
            m |= 1 << self.index[c]
        return m

    def set_owner(self, card, owner):
        i = self.index[card]
        self.domain[i] &= 1 << owner
        self.version += 1

    def exclude(self, card, owner):
        i = self.index[card]
        self.domain[i] &= ~(1 << owner)
        self.version += 1

    def exclude_solution(self, card):
        i = self.index[card]
        self.domain[i] &= (1 << self.n) - 1
        self.version += 1

    def add_clause(self, owner, cards):
        self.clauses.append((owner, self.card_mask(cards)))
        self.version += 1

    def add_not_solution(self, cards):
        self.not_solution.append(self.card_mask(cards))
        self.version += 1

    def sol_owner(self, cat_index):
        return self.n + cat_index

    # -- exact search ------------------------------------------------------
    def feasible(self, extra=None, rng=None, limit=200000):
        """Find one consistent full assignment (list owner per card) or None.

        `extra` maps card index -> required owner. Returns False if the node
        limit was hit (unknown)."""
        dom = list(self.domain)
        if extra:
            for i, o in extra.items():
                dom[i] &= 1 << o
                if not dom[i]:
                    return None
        n_cards = len(dom)
        rem = list(self.capacity)
        own = [0] * self.n_owners
        owner_of = [-1] * n_cards
        clauses = self.clauses
        notsol = self.not_solution
        sol_mask_owners = [self.n, self.n + 1, self.n + 2]
        counter = [0]

        def ok_state(unassigned):
            # Each owner must still be able to fill its remaining capacity exactly.
            avail = [0] * self.n_owners
            for i in unassigned:
                for o in bits(dom[i]):
                    avail[o] += 1
            total = 0
            for o in range(self.n_owners):
                if rem[o] < 0 or avail[o] < rem[o]:
                    return False
                total += rem[o]
            if total != len(unassigned):
                return False
            # Every unsatisfied clause needs a card that can still go to its owner.
            for o, m in clauses:
                if own[o] & m:
                    continue
                if rem[o] == 0:
                    return False
                okc = False
                for i in bits(m):
                    if owner_of[i] == -1 and (dom[i] >> o) & 1:
                        okc = True
                        break
                if not okc:
                    return False
            if notsol:
                solcards = own[sol_mask_owners[0]] | own[sol_mask_owners[1]] | own[sol_mask_owners[2]]
                for m in notsol:
                    if solcards & m == m:
                        return False
            return True

        def rec(unassigned):
            counter[0] += 1
            if counter[0] > limit:
                raise _Limit()
            if not unassigned:
                return True
            # Most constrained card first.
            best = min(unassigned, key=lambda i: popcount(dom[i]))
            rest = [i for i in unassigned if i != best]
            owners = list(bits(dom[best]))
            if rng is not None:
                rng.shuffle(owners)
            for o in owners:
                if rem[o] <= 0:
                    continue
                owner_of[best] = o
                own[o] |= 1 << best
                rem[o] -= 1
                if ok_state(rest) and rec(rest):
                    return True
                owner_of[best] = -1
                own[o] &= ~(1 << best)
                rem[o] += 1
            return False

        unassigned = list(range(n_cards))
        try:
            if not ok_state(unassigned):
                return None
            if rec(unassigned):
                return list(owner_of)
            return None
        except _Limit:
            return False

    def possible_matrix(self):
        """possible[i] = bitmask of owners card i can really have (exact)."""
        out = []
        for i in range(len(self.cards)):
            m = 0
            for o in bits(self.domain[i]):
                r = self.feasible(extra={i: o})
                if r is None:
                    continue
                m |= 1 << o  # r is a solution, or the limit hit (assume possible)
            out.append(m)
        return out

    def tighten(self):
        """Replace domains with exact possibilities. Returns the matrix."""
        mat = self.possible_matrix()
        for i, m in enumerate(mat):
            if m:
                self.domain[i] = m
        return mat

    def solution_candidates(self, mat=None):
        """Per category, the card indices that can still be in the solution."""
        if mat is None:
            mat = self.possible_matrix()
        out = [[], [], []]
        for i, m in enumerate(mat):
            k = self.cat[i]
            if (m >> (self.n + k)) & 1:
                out[k].append(i)
        return out


class _Limit(Exception):
    pass


class Sampler:
    """Swap-move MCMC over consistent full assignments."""

    def __init__(self, kn, seed=None):
        self.kn = kn
        self.rng = random.Random(seed)

    def samples(self, n_samples=1500, chains=4, burn=300, thin=4):
        kn = self.kn
        out = []
        per_chain = max(1, n_samples // chains)
        n_cards = len(kn.cards)
        for _ in range(chains):
            start = kn.feasible(rng=self.rng)
            if not start:
                continue
            owner = list(start)
            own = [0] * kn.n_owners
            for i, o in enumerate(owner):
                own[o] |= 1 << i
            clauses_by_owner = [[] for _ in range(kn.n_owners)]
            for o, m in kn.clauses:
                clauses_by_owner[o].append(m)
            notsol = kn.not_solution
            dom = kn.domain
            n = kn.n
            steps = burn + per_chain * thin
            for step in range(steps):
                a = self.rng.randrange(n_cards)
                b = self.rng.randrange(n_cards)
                oa, ob = owner[a], owner[b]
                if oa != ob and (dom[a] >> ob) & 1 and (dom[b] >> oa) & 1:
                    na = (own[oa] & ~(1 << a)) | (1 << b)
                    nb = (own[ob] & ~(1 << b)) | (1 << a)
                    good = True
                    for m in clauses_by_owner[oa]:
                        if not na & m:
                            good = False
                            break
                    if good:
                        for m in clauses_by_owner[ob]:
                            if not nb & m:
                                good = False
                                break
                    if good and notsol and (oa >= n or ob >= n):
                        save_a, save_b = own[oa], own[ob]
                        own[oa], own[ob] = na, nb
                        sol = own[n] | own[n + 1] | own[n + 2]
                        own[oa], own[ob] = save_a, save_b
                        for m in notsol:
                            if sol & m == m:
                                good = False
                                break
                    if good:
                        own[oa], own[ob] = na, nb
                        owner[a], owner[b] = ob, oa
                if step >= burn and (step - burn) % thin == 0:
                    out.append(tuple(owner))
        return out
