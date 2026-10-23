"""Bayesian beliefs over hidden role assignments in 7-player Secret Dictator.

A hypothesis is (dictator seat, frozenset of the two ordinary fascists).
Evidence is folded in with soft likelihood models of how liberals and
fascists tend to behave; every model is mixed with noise so no single
(possibly misparsed) observation can be decisive, except hard facts
(our own investigation, "confirmed not dictator", executions that did not
end the game).
"""
import math
from itertools import combinations

L, F, D = "L", "F", "D"

# Behaviour parameters (probability a team member takes the fascist option).
AGGR = {F: 0.75, D: 0.5}
LIB_ERR = 0.05     # liberal bots playing a fascist card when they had a choice
CLAIM_TRUTH_LIB = 0.88
VOTE_YES_LIB = 0.62


def hypotheses(seats, me=None, role=None, known_fascists=(), known_dictator=None):
    if role == "fascist":
        fas = frozenset([me] + [s for s in known_fascists if s != me])
        return [(known_dictator, fas)]
    others = [s for s in seats if s != me] if me else list(seats)
    out = []
    if role == "dictator":
        for pair in combinations(others, 2):
            out.append((me, frozenset(pair)))
        return out
    for d in others:
        rest = [s for s in others if s != d]
        for pair in combinations(rest, 2):
            out.append((d, frozenset(pair)))
    return out


def role_of(h, seat):
    d, fas = h
    if seat == d:
        return D
    if seat in fas:
        return F
    return L


def team(h, seat):
    return role_of(h, seat) != L


def binom3(p):
    """P(k liberal cards among 3 draws)."""
    q = 1 - p
    return [q ** 3, 3 * p * q * q, 3 * p * p * q, p ** 3]


def pres_pass(role, k):
    """Distribution over j = liberal cards passed to the chancellor, given k drawn."""
    honest = {0: 0, 1: 1, 2: 2, 3: 2}[k]
    if role == L:
        if honest != max(0, k - 1) and k not in (0, 3):
            return [(honest, 1 - LIB_ERR), (max(0, k - 1), LIB_ERR)]
        return [(honest, 1.0)]
    a = AGGR[role]
    nasty = max(0, min(2, k - 1)) if k < 3 else 2
    if nasty == honest:
        return [(honest, 1.0)]
    return [(nasty, a), (honest, 1 - a)]


def chanc_enact(role, j):
    """Distribution over the enacted policy given j liberal cards received."""
    if j == 0:
        return [("fascist", 1.0)]
    if j == 2:
        return [("liberal", 1.0)]
    if role == L:
        return [("liberal", 1 - LIB_ERR), ("fascist", LIB_ERR)]
    a = AGGR[role]
    return [("fascist", a), ("liberal", 1 - a)]


def claim_prob(role, claimed, truth, enacted, n_values):
    """P(claim | truth) for a draw/receive claim with n_values possible values."""
    other = 1.0 / n_values
    if role == L:
        return CLAIM_TRUTH_LIB * (claimed == truth) + (1 - CLAIM_TRUTH_LIB) * other
    if enacted == "fascist":
        # Team members covering a fascist policy tend to claim "no liberals".
        return 0.40 * (claimed == truth) + 0.45 * (claimed == 0) + 0.15 * other
    return 0.78 * (claimed == truth) + 0.22 * other


class Belief:
    def __init__(self, hyps):
        self.hyps = hyps
        self.logw = [0.0] * len(hyps)

    def update(self, fn, floor=1e-4):
        """Multiply each hypothesis by fn(h); fn returns a likelihood >= 0."""
        new = []
        for h, lw in zip(self.hyps, self.logw):
            v = fn(h)
            new.append(lw + math.log(max(v, floor)) if v is not None else lw)
        self.logw = new
        self._normalise()

    def hard(self, pred):
        """Remove hypotheses violating a certain fact (unless that removes all)."""
        keep = [i for i, h in enumerate(self.hyps) if pred(h)]
        if not keep:
            return
        self.hyps = [self.hyps[i] for i in keep]
        self.logw = [self.logw[i] for i in keep]
        self._normalise()

    def _normalise(self):
        if not self.logw:
            return
        m = max(self.logw)
        self.logw = [lw - m for lw in self.logw]

    def probs(self):
        ws = [math.exp(lw) for lw in self.logw]
        z = sum(ws) or 1.0
        return [w / z for w in ws]

    def p_team(self, seat):
        return sum(p for h, p in zip(self.hyps, self.probs()) if team(h, seat))

    def p_dictator(self, seat):
        return sum(p for h, p in zip(self.hyps, self.probs()) if h[0] == seat)

    def p_any_team(self, seats):
        return sum(p for h, p in zip(self.hyps, self.probs()) if any(team(h, s) for s in seats if s))

    def summary(self, seats):
        return {s: (round(self.p_team(s), 2), round(self.p_dictator(s), 2)) for s in seats}


# ----------------------------------------------------------------------
# Evidence from the game state.
# ----------------------------------------------------------------------
def gov_likelihood(h, g, me):
    """Likelihood of what we saw of one elected government's legislation."""
    if not g.elected or g.chancellor is None or (g.enacted is None and not g.vetoed):
        return None
    rp, rc = role_of(h, g.president), role_of(h, g.chancellor)
    pl = g.pile_pl if g.pile_pl is not None else 6 / 17
    pk = binom3(pl)
    enacted = g.enacted
    pclaim = g.claims.get(g.president, {}).get("drawn_l")
    cclaim = g.claims.get(g.chancellor, {}).get("received_l")
    if g.president == me:
        pclaim = None
    if g.chancellor == me:
        cclaim = None

    # Our own private knowledge pins down parts of the chain.
    known_k = g.drawn.count("liberal") if g.drawn else None
    known_j = None
    if g.drawn and g.discarded:
        rest = list(g.drawn)
        rest.remove(g.discarded)
        known_j = rest.count("liberal")
    if g.received:
        known_j = g.received.count("liberal")

    total = 0.0
    for k in range(4):
        if known_k is not None and k != known_k:
            continue
        p0 = 1.0 if known_k is not None else pk[k]
        if p0 == 0:
            continue
        if g.president == me and known_j is not None:
            pass_dist = [(known_j, 1.0)]
        else:
            pass_dist = pres_pass(rp, k)
        for j, pj in pass_dist:
            if known_j is not None and j != known_j:
                continue
            if g.vetoed:
                enact_dist = [(None, 1.0)]
            elif g.chancellor == me:
                enact_dist = [(enacted, 1.0)]
            else:
                enact_dist = chanc_enact(rc, j)
            for x, px in enact_dist:
                if x != enacted:
                    continue
                pc = 1.0
                if pclaim is not None:
                    pc *= claim_prob(rp, pclaim, k, enacted, 4)
                if cclaim is not None:
                    pc *= claim_prob(rc, cclaim, j, enacted, 3)
                total += p0 * pj * px * pc
    return 0.85 * total + 0.15 * _baseline(g, pk, pclaim, cclaim)


def _baseline(g, pk, pclaim, cclaim):
    """Role-independent noise term so a single government can't be decisive."""
    v = 0.5
    if pclaim is not None:
        v *= 0.25
    if cclaim is not None:
        v *= 0.33
    return v


def vote_likelihood(h, g, fascist_count):
    if not g.votes or g.chancellor is None:
        return None
    gov_team = team(h, g.president) or team(h, g.chancellor)
    dict_chanc = fascist_count >= 3 and role_of(h, g.chancellor) == D
    like = 1.0
    for voter, vote in g.votes.items():
        if voter in (g.president, g.chancellor):
            continue
        if team(h, voter):
            if dict_chanc:
                py = 0.95
            elif gov_team:
                py = 0.82
            else:
                py = 0.5
        else:
            py = VOTE_YES_LIB
            if dict_chanc:
                py = 0.5
        like *= py if vote == "yes" else (1 - py)
    return like


def nomination_likelihood(h, g):
    if not g.chancellor or not g.eligible or g.chancellor not in g.eligible:
        return None
    if not team(h, g.president):
        return 1.0 / len(g.eligible)
    ws = {s: (1.7 if team(h, s) else 1.0) for s in g.eligible}
    return ws[g.chancellor] / sum(ws.values())


def investigation_claim_likelihood(h, investigator, target, claimed):
    t_team = team(h, target)
    truth = "fascist" if t_team else "liberal"
    if not team(h, investigator):
        return 0.93 if claimed == truth else 0.07
    if t_team:
        return 0.75 if claimed == "liberal" else 0.25
    return 0.45 if claimed == "fascist" else 0.55


def execution_likelihood(h, president, target):
    if not team(h, president):
        return 1.0
    return 0.3 if team(h, target) else 1.0


def build_belief(st, public=False, claims=None):
    """Belief from my perspective (public=False) or a neutral observer's (public=True)."""
    if public:
        hyps = hypotheses(st.seats)
        me = None
    else:
        hyps = hypotheses(st.seats, st.me, st.role, st.known_fascists, st.known_dictator)
        me = st.me
    b = Belief(hyps)
    if len(hyps) <= 1:
        return b
    b.hard(lambda h: h[0] not in st.not_dictator)
    for g in st.govs:
        if claims is not None:
            g.claims = claims.get(g.round, {})
        priv_g = g if not public else _strip_private(g)
        b.update(lambda h, g=priv_g: gov_likelihood(h, g, me))
        b.update(lambda h, g=g: vote_likelihood(h, g, g.fascist_before))
        b.update(lambda h, g=g: nomination_likelihood(h, g))
    for rnd, inv, tgt, faction in st.investigations:
        if not public and inv == st.me and faction:
            truth = faction
            b.hard(lambda h, t=tgt, f=truth: (team(h, t)) == (f == "fascist"))
        else:
            claimed = None
            if claims is not None:
                claimed = claims.get(("investigation", rnd), {}).get(inv)
            if claimed in ("liberal", "fascist"):
                b.update(lambda h, i=inv, t=tgt, c=claimed: investigation_claim_likelihood(h, i, t, c))
    for rnd, pres, tgt in st.executions:
        b.update(lambda h, p=pres, t=tgt: execution_likelihood(h, p, t))
    return b


class _PublicGov:
    pass


def _strip_private(g):
    pg = _PublicGov()
    pg.__dict__.update(g.__dict__)
    pg.drawn = None
    pg.discarded = None
    pg.received = None
    return pg
