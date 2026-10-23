"""Clue agent.

Pipeline per decision:
  history -> ClueState -> Knowledge (exact constraints) -> tighten (exact
  per-card possibilities) -> Sampler (consistent deals) -> decisions:
  * accuse when the solution is uniquely determined (or gamble at the very end);
  * suggestions maximise expected information about the solution;
  * movement trades suggestion value against travel time;
  * card showing reuses cards the suggester has already seen.
"""
import math
import random

from common.io import log
from common.timer import OutOfTime
from clue.deduce import Sampler, bits
from clue.state import ClueState

GAMMA = 0.72


def dice_reach_table(max_turns=8, max_dist=80):
    """P[t][d] = probability that t rolls of 2d6 total at least d."""
    single = {}
    for a in range(1, 7):
        for b in range(1, 7):
            single[a + b] = single.get(a + b, 0) + 1 / 36
    table = [[1.0] * (max_dist + 1)]
    dist = {0: 1.0}
    for _ in range(max_turns):
        new = {}
        for s, p in dist.items():
            for v, q in single.items():
                new[s + v] = new.get(s + v, 0) + p * q
        dist = new
        row = []
        for d in range(max_dist + 1):
            row.append(sum(p for s, p in dist.items() if s >= d))
        table.append(row)
    return table


REACH = dice_reach_table()


def travel_discount(d, turns_left):
    """Expected discount for arriving at a door d steps away, starting next turn."""
    if d is None:
        return 0.0
    d = min(d, len(REACH[0]) - 1)
    total = 0.0
    prev = 0.0
    for t in range(1, min(turns_left, len(REACH) - 1) + 1):
        p = REACH[t][d]
        total += (p - prev) * (GAMMA ** t)
        prev = p
    return total


class Analysis:
    """Deduction results for one decision."""

    def __init__(self, st, budget, n_samples=1200):
        self.st = st
        self.kn = st.knowledge()
        self.idx = {s: i for i, s in enumerate(st.seats)}
        self.me = self.idx[st.me]
        n = self.kn.n
        try:
            self.mat = self.kn.tighten()
        except Exception as e:
            log("tighten failed", repr(e))
            self.mat = list(self.kn.domain)
        self.cands = [[], [], []]
        for i, m in enumerate(self.mat):
            k = self.kn.cat[i]
            if (m >> (n + k)) & 1:
                self.cands[k].append(i)
        self.certain = all(len(c) == 1 for c in self.cands)
        self.samples = []
        if not self.certain and not budget.expired():
            try:
                self.samples = Sampler(self.kn, seed=random.randrange(1 << 30)).samples(n_samples=n_samples)
            except Exception as e:
                log("sampling failed", repr(e))
        self.sol_of = []
        self.triple_prob = {}
        for smp in self.samples:
            sol = [0, 0, 0]
            for i, o in enumerate(smp):
                if o >= n:
                    sol[o - n] = i
            t = tuple(sol)
            self.sol_of.append(t)
            self.triple_prob[t] = self.triple_prob.get(t, 0) + 1
        total = len(self.samples)
        for t in self.triple_prob:
            self.triple_prob[t] /= total
        self._entropy = None

    def best_triple(self):
        if self.certain:
            return tuple(c[0] for c in self.cands), 1.0
        if not self.triple_prob:
            # No samples: pick the first candidate in each category.
            return tuple(c[0] for c in self.cands if c), 0.0
        t = max(self.triple_prob, key=self.triple_prob.get)
        return t, self.triple_prob[t]

    def card_name(self, i):
        return self.kn.cards[i]

    # -- opponent progress -------------------------------------------------
    def opponent_threat(self, max_samples=300):
        """P(at least one active opponent can already name the solution).

        For each sampled deal we reconstruct what each opponent has seen (own hand,
        cards shown to them, unrefuted suggestions) and apply simple elimination,
        which under-estimates their knowledge, so this is a lower bound."""
        st = self.st
        if not self.samples:
            return 0.0
        kn = self.kn
        n = kn.n
        opps = [self.idx[s] for s in st.seats if s != st.me and s not in st.eliminated]
        if not opps:
            return 0.0
        cat_cards = [[i for i in range(len(kn.cards)) if kn.cat[i] == k] for k in range(3)]
        sugs = []
        for sg in st.suggestions:
            try:
                tri = [kn.index[c] for c in sg.cards()]
            except KeyError:
                continue
            sugs.append((self.idx.get(sg.suggester), tri,
                         self.idx.get(sg.refuter) if sg.refuter else None,
                         kn.index.get(sg.card) if sg.card else None))
        step = max(1, len(self.samples) // max_samples)
        hits = 0
        total = 0
        for smp in self.samples[::step]:
            total += 1
            any_certain = False
            for o in opps:
                known = {i for i, ow in enumerate(smp) if ow == o}
                sol_known = set()
                for sugg, tri, ref, card in sugs:
                    if sugg != o:
                        continue
                    if ref is None:
                        sol_known.update(c for c in tri if smp[c] != o)
                    else:
                        if card is None:
                            held = [c for c in tri if smp[c] == ref]
                            card = held[0] if held else None
                        if card is not None:
                            known.add(card)
                certain = True
                for k in range(3):
                    if any(c in sol_known for c in cat_cards[k]):
                        continue
                    if sum(1 for c in cat_cards[k] if c not in known) != 1:
                        certain = False
                        break
                if certain:
                    any_certain = True
                    break
            hits += any_certain
        return hits / total if total else 0.0

    # -- suggestion value --------------------------------------------------
    def suggestion_value(self, s, w, r, order):
        """Expected information (bits) about the solution from suggesting (s, w, r).

        `order` = refutation order of player indices after the suggester."""
        if not self.samples:
            return 0.0
        n = self.kn.n
        counts = {}   # outcome -> [weight, {cat0 card: w}, {cat1}, {cat2}]
        triple = (s, w, r)
        for smp, sol in zip(self.samples, self.sol_of):
            outcome = None
            for p in order:
                held = [c for c in triple if smp[c] == p]
                if held:
                    outcome = (p, held)
                    break
            if outcome is None:
                keys = [(None, None)]
            else:
                keys = [(outcome[0], c) for c in outcome[1]]
            wgt = 1.0 / len(keys)
            for key in keys:
                e = counts.get(key)
                if e is None:
                    e = counts[key] = [0.0, {}, {}, {}]
                e[0] += wgt
                for k in range(3):
                    dct = e[k + 1]
                    dct[sol[k]] = dct.get(sol[k], 0.0) + wgt
        total = float(len(self.samples))
        prior = self.marginal_entropy()
        post = [0.0, 0.0, 0.0]
        h_out = 0.0
        for e in counts.values():
            pw = e[0] / total
            h_out -= pw * math.log2(pw)
            for k in range(3):
                h = 0.0
                for v in e[k + 1].values():
                    q = v / e[0]
                    h -= q * math.log2(q)
                post[k] += pw * h
        gain = sum(prior[k] - post[k] for k in range(3))
        return gain + 0.12 * h_out

    def marginal_entropy(self):
        if self._entropy is None:
            out = []
            total = float(len(self.samples)) or 1.0
            for k in range(3):
                cnt = {}
                for sol in self.sol_of:
                    cnt[sol[k]] = cnt.get(sol[k], 0) + 1
                h = 0.0
                for v in cnt.values():
                    q = v / total
                    h -= q * math.log2(q)
                out.append(h)
            self._entropy = out
        return self._entropy


class Agent:
    threat_aware = True

    def __init__(self, seat):
        self.seat = seat
        self.rng = random.Random()

    # ------------------------------------------------------------------
    def decide(self, msg, budget):
        st = ClueState(msg.get("history") or [])
        kind = (msg.get("decision") or {}).get("kind")
        if kind == "show-card":
            return self.show_card(st)
        if kind == "make-suggestion":
            an = Analysis(st, budget)
            room = st.my_pos[1] if st.my_pos and st.my_pos[0] == "room" else None
            return self.suggest(st, an, room, budget)
        if kind == "accuse-or-end":
            an = Analysis(st, budget)
            acc = self.maybe_accuse(st, an, after_turn=True)
            return acc or {"type": "turn-ended"}
        if kind == "take-turn":
            an = Analysis(st, budget)
            acc = self.maybe_accuse(st, an, after_turn=False)
            if acc:
                return acc
            return self.take_turn(st, an, budget)
        raise ValueError("unknown decision kind %r" % kind)

    # ------------------------------------------------------------------
    def maybe_accuse(self, st, an, after_turn):
        triple, p = an.best_triple()
        if len(triple) != 3:
            return None
        if not an.certain:
            # Only gamble at the end of our turn (every turn ends with accuse-or-end),
            # and only when time is nearly up: a game with no winner is worth nothing.
            if not after_turn:
                return None
            others = [s for s in st.seats if s != st.me and s not in st.eliminated]
            if not others:
                return None  # last detective standing wins without risk
            left = st.my_remaining_turns() - 1
            if left <= 0:
                need = 0.0
            elif left == 1:
                need = 0.7
            else:
                need = 1.01
            if self.threat_aware and p >= 0.5:
                # Waiting is only worth it if no opponent solves before our next turn.
                threat = an.opponent_threat()
                need = min(need, max(0.5, 0.95 * (1.0 - threat)))
            if p <= need:
                return None
            log("clue gamble", round(p, 3), left)
        names = [an.card_name(i).split(":", 1)[1] for i in triple]
        return {"type": "accusation-made", "suspect": names[0], "weapon": names[1], "room": names[2]}

    def refute_order(self, st, an):
        n = len(st.seats)
        me = an.me
        return [(me + k) % n for k in range(1, n)]

    def suggest(self, st, an, room, budget):
        if room is None:
            raise ValueError("not in a room")
        best = self.best_suggestion(st, an, room, budget)
        return {"type": "suggestion-made", "suspect": best[0], "weapon": best[1], "room": room}

    def best_suggestion(self, st, an, room, budget):
        """Returns (suspect, weapon, value)."""
        kn = an.kn
        r = kn.index["room:" + room]
        order = self.refute_order(st, an)
        best = None
        sus_idx = [kn.index["suspect:" + s] for s in st.suspects]
        wep_idx = [kn.index["weapon:" + w] for w in st.weapons]
        top, _ = an.best_triple()
        for s in sus_idx:
            for w in wep_idx:
                if budget.expired() and best is not None:
                    break
                v = an.suggestion_value(s, w, r, order)
                # Tie-breaks: prefer likely-solution cards, then pulling active opponents away.
                tb = 0.0
                if len(top) == 3:
                    tb += 0.001 * ((s == top[0]) + (w == top[1]))
                name = kn.cards[s].split(":", 1)[1]
                seat = st.char_seat.get(name)
                if seat and seat != st.me and seat not in st.eliminated:
                    tb += 0.0005
                if best is None or v + tb > best[2]:
                    best = (name, kn.cards[w].split(":", 1)[1], v + tb)
        return best

    def room_values(self, st, an, budget):
        vals = {}
        for room in st.rooms:
            if budget.expired():
                break
            b = self.best_suggestion(st, an, room, budget.sub(1.5))
            vals[room] = b[2] if b else 0.0
        return vals

    # ------------------------------------------------------------------
    def take_turn(self, st, an, budget):
        board = st.board
        pos = st.my_pos
        roll = sum(st.roll)
        blocked = st.blocked()
        turns_left = max(1, st.my_remaining_turns() - 1)
        values = self.room_values(st, an, budget.sub(budget.remaining() * 0.6))
        default = max(values.values()) if values else 0.0
        val = lambda room: values.get(room, default * 0.5)

        options = []  # (score, response)
        if pos[0] == "room" and st.pulled:
            options.append((val(pos[1]) + 1e-6, {"type": "suggestion-made", "suspect": None, "room": pos[1]}))

        if pos[0] == "room" and pos[1] in board.passages:
            dest = board.passages[pos[1]]
            options.append((val(dest), {"type": "pawn-moved", "path": [], "enterRoom": dest, "useSecretPassage": True}))

        entries = board.room_entries(pos, roll, blocked)
        for room, path in entries.items():
            if pos[0] == "room" and room == pos[1]:
                continue
            if board.validate(pos, path, roll, blocked, enter_room=room):
                options.append((val(room), {"type": "pawn-moved", "path": self._cells(path), "enterRoom": room}))

        # Corridor endpoints: value = best room reachable later, discounted by distance.
        targets = board.corridor_targets(pos, roll, blocked)
        scored = []
        for cell in targets:
            best = 0.0
            for room in st.rooms:
                d = board.dist_to_room(room).get(cell)
                if d is None:
                    continue
                v = val(room) * travel_discount(d, turns_left)
                if v > best:
                    best = v
            scored.append((best, cell))
        scored.sort(reverse=True)
        tried = 0
        for v, cell in scored:
            if tried >= 12 or budget.expired():
                break
            tried += 1
            path = board.exact_path(pos, roll, cell, blocked)
            if path and board.validate(pos, path, roll, blocked):
                options.append((v, {"type": "pawn-moved", "path": self._cells(path)}))
                break

        if not options:
            # Any legal corridor move at all?
            path = self.any_corridor_path(st, roll, blocked, budget)
            if path:
                return {"type": "pawn-moved", "path": self._cells(path)}
            return {"type": "turn-ended"}

        options.sort(key=lambda o: -o[0])
        choice = options[0][1]
        if choice["type"] == "suggestion-made":
            b = self.best_suggestion(st, an, pos[1], budget)
            choice = {"type": "suggestion-made", "suspect": b[0], "weapon": b[1], "room": pos[1]}
        return choice

    def any_corridor_path(self, st, roll, blocked, budget):
        board = st.board
        pos = st.my_pos
        for cell in board.corridor_targets(pos, roll, blocked):
            if budget.expired():
                break
            path = board.exact_path(pos, roll, cell, blocked, node_limit=5000)
            if path and board.validate(pos, path, roll, blocked):
                return path
        return None

    @staticmethod
    def _cells(path):
        return [{"row": r, "column": c} for r, c in path]

    # ------------------------------------------------------------------
    def show_card(self, st):
        sg = None
        for s in reversed(st.suggestions):
            if s.refuter == st.me:
                sg = s
                break
        if sg is None:
            raise ValueError("no suggestion to refute")
        hand = set(st.hand)
        matching = [c for c in sg.cards() if c in hand]
        if not matching:
            raise ValueError("nothing to show")
        seen_by_them = st.shown_by_me.get(sg.suggester, set())
        shown_any = {}
        for cards in st.shown_by_me.values():
            for c in cards:
                shown_any[c] = shown_any.get(c, 0) + 1

        def key(c):
            return (c not in seen_by_them, -shown_any.get(c, 0), c.startswith("room:") is False, c)

        return {"type": "card-shown", "card": sorted(matching, key=key)[0]}

    # ------------------------------------------------------------------
    def fallback(self, msg):
        st = ClueState(msg.get("history") or [])
        kind = (msg.get("decision") or {}).get("kind")
        if kind == "show-card":
            return self.show_card(st)
        if kind == "accuse-or-end":
            return {"type": "turn-ended"}
        pos = st.my_pos
        if kind == "make-suggestion":
            hand = set(st.hand)
            s = next((x for x in st.suspects if "suspect:" + x not in hand), st.suspects[0])
            w = next((x for x in st.weapons if "weapon:" + x not in hand), st.weapons[0])
            return {"type": "suggestion-made", "suspect": s, "weapon": w, "room": pos[1]}
        # take-turn
        board = st.board
        roll = sum(st.roll)
        blocked = st.blocked()
        if pos[0] == "room" and st.pulled:
            hand = set(st.hand)
            s = next((x for x in st.suspects if "suspect:" + x not in hand), st.suspects[0])
            w = next((x for x in st.weapons if "weapon:" + x not in hand), st.weapons[0])
            return {"type": "suggestion-made", "suspect": s, "weapon": w, "room": pos[1]}
        if pos[0] == "room" and pos[1] in board.passages:
            return {"type": "pawn-moved", "path": [], "enterRoom": board.passages[pos[1]], "useSecretPassage": True}
        for room, path in board.room_entries(pos, roll, blocked).items():
            if board.validate(pos, path, roll, blocked, enter_room=room):
                return {"type": "pawn-moved", "path": self._cells(path), "enterRoom": room}
        for cell in board.corridor_targets(pos, roll, blocked)[:40]:
            path = board.exact_path(pos, roll, cell, blocked, node_limit=3000)
            if path:
                return {"type": "pawn-moved", "path": self._cells(path)}
        return {"type": "turn-ended"}
