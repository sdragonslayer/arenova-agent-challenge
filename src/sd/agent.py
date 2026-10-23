"""Secret Dictator agent."""
import random

from common.io import log
from common.llm import LLM
from sd.belief import build_belief
from sd.claims import ClaimTracker, context_round
from sd.policy import Brain
from sd.state import SDState
from sd.talk import Talker, chancellor_claim, clip, president_claim


def seat_num(s):
    try:
        return int(str(s).split("-")[-1])
    except ValueError:
        return 0


def next_from(me, cands):
    """Baseline order: ascending seat number starting after `me`, wrapping."""
    if not cands:
        return None
    m = seat_num(me)
    return sorted(cands, key=lambda s: ((seat_num(s) - m - 1) % 7, s))[0]


class Agent:
    def __init__(self, seat):
        self.seat = seat
        self.rng = random.Random()
        self.llm = LLM(max_calls=70)
        self.claims = ClaimTracker(self.llm)
        self.talker = Talker(self.llm)
        self.my_claims = {}         # round -> dict(drawn_l, passed_l) or (received_l, enacted)
        self.inv_claims = {}        # target -> announced faction
        self.inv_announced = set()

    # ------------------------------------------------------------------
    def decide(self, msg, budget):
        st = SDState(msg.get("history") or [])
        kind = (msg.get("decision") or {}).get("kind")
        first_round = st.round <= 1 and not any(g.enacted for g in st.govs)
        # LLM claim extraction only when there's comfortable time and it's not round 1.
        if not first_round and budget.remaining() > 20 and kind in ("discuss", "vote", "nominate-chancellor"):
            self.claims.llm_pass(st, timeout=min(12.0, budget.remaining() - 12))
        claims = self.claims.build(st)
        mine = build_belief(st, public=False, claims=claims)
        public = build_belief(st, public=True, claims=claims)
        brain = Brain(st, mine, public, self.rng)
        self._log_beliefs(st, brain, kind)
        handler = getattr(self, "do_" + kind.replace("-", "_"), None)
        if handler is None:
            raise ValueError("unknown kind %r" % kind)
        talk_budget = 0.0 if first_round else max(0.0, budget.remaining() - 5.0)
        return handler(st, brain, claims, talk_budget)

    def _log_beliefs(self, st, brain, kind):
        if st.role == "fascist":
            return
        try:
            summ = {s[-1]: round(brain.sus(s), 2) for s in st.others_alive()}
            log("sd r%d %s %s sus=%s" % (st.round, kind, st.role, summ), low_priority=True)
        except Exception:
            pass

    # -- actions ---------------------------------------------------------
    def do_nominate_chancellor(self, st, brain, claims, tb):
        target = brain.nominate()
        elig = st.eligible_chancellors(st.me)
        if target not in elig and elig:
            target = elig[0]
        points = [self._inv_announcement(st, brain)]
        points.append("I nominate %s." % target)
        if st.role == "liberal" and st.fascist >= 3:
            points.append("After three Fascist policies I only trust players I'm confident are not the Dictator.")
        text = self.talker.render(points, keep="I nominate %s" % target, budget_s=0)
        out = {"type": "chancellor-nominated", "target": target}
        if text:
            out["statement"] = clip(text)
        return out

    def do_vote(self, st, brain, claims, tb):
        g = st.cur
        return {"type": "vote-cast", "vote": brain.vote(g.president, g.chancellor)}

    def do_president_discard(self, st, brain, claims, tb):
        g = st.cur
        drawn = list(g.drawn or [])
        if not drawn:
            raise ValueError("no drawn policies visible")
        pol = brain.president_discard(drawn, g.chancellor)
        if pol not in drawn:
            pol = drawn[0]
        return {"type": "president-discarded", "policy": pol}

    def do_chancellor_legislate(self, st, brain, claims, tb):
        g = st.cur
        received = list(g.received or [])
        if not received:
            raise ValueError("no received policies visible")
        veto_ok = st.veto_unlocked and not g.veto_rejected and not g.veto_requested
        choice = brain.chancellor_enact(received, g.president, veto_ok)
        if choice == "veto":
            if veto_ok:
                return {"type": "veto-requested"}
            choice = "fascist" if "fascist" in received else received[0]
        if choice not in received:
            choice = received[0]
        return {"type": "policy-enacted", "policy": choice}

    def do_president_veto_response(self, st, brain, claims, tb):
        g = st.cur
        passed_l = None
        if g.drawn and g.discarded:
            rest = list(g.drawn)
            rest.remove(g.discarded)
            passed_l = rest.count("liberal")
        return {"type": "veto-responded", "accepted": bool(brain.veto_response(passed_l, g.chancellor))}

    def do_investigate_player(self, st, brain, claims, tb):
        cands = [s for s in st.others_alive() if s not in st.investigated] or st.others_alive()
        t = brain.investigate()
        if t not in cands:
            t = cands[0]
        return {"type": "loyalty-investigated", "target": t}

    def do_special_election(self, st, brain, claims, tb):
        t = brain.special_election()
        if t not in st.others_alive():
            t = st.others_alive()[0]
        return {"type": "special-election-called", "target": t,
                "statement": "I choose %s as the next President." % t}

    def do_execute_player(self, st, brain, claims, tb):
        t = brain.execute()
        if t not in st.others_alive():
            t = st.others_alive()[0]
        return {"type": "player-executed", "target": t}

    # -- discussion --------------------------------------------------------
    def do_discuss(self, st, brain, claims, tb):
        ctx = None
        for it in reversed(st.items):
            ev = it.get("event") or {}
            if ev.get("type") == "discussion-started":
                ctx = ev.get("context")
                break
        rnd = context_round(ctx) if ctx else st.round
        g = st.gov_by_round(rnd) or st.cur
        mine_in_ctx = sum(1 for (_, f, c, _) in st.statements if f == st.me and c == ctx)
        mine_in_ctx += sum(1 for (_, s, c) in st.passes if s == st.me and c == ctx)
        slot = mine_in_ctx + 1
        in_gov = g is not None and st.me in (g.president, g.chancellor)

        points = []
        keep = None
        if in_gov and slot == 1:
            claim = self._gov_claim(st, brain, g)
            points.append(claim)
            keep = claim.split(" (")[0] if "(" in claim else claim
        inv = self._inv_announcement(st, brain)
        if inv:
            points.append(inv)
            keep = keep or inv
        if slot <= 2:
            points.extend(self._read_points(st, brain, claims, g, slot, in_gov))
        elif in_gov:
            conflict = self._conflict_text(st, claims, g)
            if conflict:
                points.append("I stand by my claim.")
        points = [p for p in points if p]
        if not points:
            return {"type": "discussion-contributed", "statement": None}
        text = self.talker.render(points, keep=keep, budget_s=tb if slot == 1 else 0)
        return {"type": "discussion-contributed", "statement": clip(text) if text else None}

    def _gov_claim(self, st, brain, g):
        if g.round in self.my_claims:
            c = self.my_claims[g.round]
        else:
            c = self._compute_claim(st, brain, g)
            self.my_claims[g.round] = c
        if "drawn_l" in c:
            return president_claim(c["drawn_l"], c["passed_l"])
        if c.get("veto"):
            return "As Chancellor I received FF (2 Fascist) and requested a veto."
        return chancellor_claim(c["received_l"], c["enacted"])

    def _compute_claim(self, st, brain, g):
        lib = st.role == "liberal"
        team = brain.team
        if g.president == st.me and g.drawn:
            k = g.drawn.count("liberal")
            rest = list(g.drawn)
            if g.discarded in rest:
                rest.remove(g.discarded)
            j = rest.count("liberal")
            if lib or st.role == "dictator":
                if st.role == "dictator" and j < min(k, 2):
                    # We discarded a liberal; claim the draw that makes our pass look forced.
                    return {"drawn_l": j, "passed_l": j}
                return {"drawn_l": k, "passed_l": j}
            if g.enacted == "fascist" and j >= 1 and g.chancellor in team:
                return {"drawn_l": 0, "passed_l": 0}       # cover our chancellor
            if j < min(k, 2):
                return {"drawn_l": j, "passed_l": j}       # make the pass look forced
            return {"drawn_l": k, "passed_l": j}
        if g.chancellor == st.me and g.received:
            j = g.received.count("liberal")
            if g.vetoed:
                return {"veto": True}
            enacted = g.enacted or "fascist"
            if not lib and enacted == "fascist" and j >= 1:
                return {"received_l": 0, "enacted": enacted}
            return {"received_l": j, "enacted": enacted}
        return {"drawn_l": 0, "passed_l": 0}

    def _inv_announcement(self, st, brain):
        for rnd, inv, tgt, faction in st.investigations:
            if inv != st.me or tgt in self.inv_announced or not faction:
                continue
            claim = self.inv_claims.get(tgt)
            if claim is None:
                claim = brain.investigation_claim(tgt, faction)
                self.inv_claims[tgt] = claim
            self.inv_announced.add(tgt)
            return "I investigated %s: they are %s." % (tgt, claim.capitalize())
        return None

    def _conflict_text(self, st, claims, g):
        if g is None or not g.elected:
            return None
        c = claims.get(g.round, {})
        pc = c.get(g.president, {}).get("drawn_l")
        cc = c.get(g.chancellor, {}).get("received_l")
        if g.president == st.me and g.round in self.my_claims:
            pc = self.my_claims[g.round].get("drawn_l")
        if g.chancellor == st.me and g.round in self.my_claims:
            cc = self.my_claims[g.round].get("received_l")
        if pc is None or cc is None:
            if cc is not None and cc >= 1 and g.enacted == "fascist" and g.chancellor != st.me:
                return "%s says they had a Liberal but enacted Fascist." % g.chancellor
            return None
        implied = {0: 0, 1: 1, 2: 2, 3: 2}[pc]
        if implied != cc:
            return "Claims conflict: %s says they passed %s, %s says they got %s." % (
                g.president, "F" * (2 - implied) + "L" * implied, g.chancellor, "F" * (2 - cc) + "L" * cc)
        return None

    def _read_points(self, st, brain, claims, g, slot, in_gov):
        pts = []
        others = st.others_alive()
        if not others:
            return pts
        conflict = self._conflict_text(st, claims, g)
        if st.role == "liberal":
            ranked = sorted(others, key=lambda s: -brain.sus(s))
            top = ranked[0]
            if slot == 1:
                if conflict and not in_gov:
                    pts.append(conflict)
                    liar = max((g.president, g.chancellor), key=brain.sus)
                    pts.append("I believe %s is the liar." % liar)
                elif g is not None and g.enacted == "liberal" and not in_gov:
                    pts.append("Liberal policy from %s and %s; that raises my trust in both." % (g.president, g.chancellor))
                elif g is not None and g.enacted == "fascist" and not in_gov:
                    pts.append("A Fascist policy came from %s/%s; I'm noting that." % (g.president, g.chancellor))
                return pts
            if brain.sus(top) >= 0.45:
                trusted = min(others, key=brain.sus)
                pts.append("My read: %s is the most suspicious (%d%%); %s looks most trustworthy." % (
                    top, round(100 * brain.sus(top)), trusted))
                if st.fascist >= 3:
                    pts.append("Don't elect anyone who might be the Dictator.")
            return pts
        # Fascist team (ordinary fascist or dictator): steer suspicion toward liberals.
        if st.role == "fascist":
            libs = [s for s in others if s not in brain.team]
        else:
            libs = sorted(others, key=lambda s: brain.mine.p_team(s))[:4]
        if not libs:
            return pts
        target = max(libs, key=lambda s: (brain.psus(s), s))
        if slot == 1:
            if conflict and not in_gov:
                liar = g.president if g.president in libs else g.chancellor
                pts.append(conflict)
                pts.append("I believe %s is the liar." % liar)
            elif g is not None and g.enacted == "liberal" and not in_gov:
                pts.append("Liberal policy from %s and %s; good to see." % (g.president, g.chancellor))
            elif g is not None and g.enacted == "fascist" and not in_gov:
                if g.president in libs or g.chancellor in libs:
                    who = g.president if g.president in libs else g.chancellor
                    pts.append("A Fascist policy with %s in office; I'm watching %s." % (who, who))
            return pts
        if brain.psus(target) >= 0.3 or self.rng.random() < 0.5:
            pts.append("My read: %s is the most suspicious right now." % target)
        return pts

    # ------------------------------------------------------------------
    def fallback(self, msg):
        st = SDState(msg.get("history") or [])
        kind = (msg.get("decision") or {}).get("kind")
        g = st.cur
        if kind == "nominate-chancellor":
            t = next_from(st.me, st.eligible_chancellors(st.me)) or next_from(st.me, st.others_alive())
            return {"type": "chancellor-nominated", "target": t}
        if kind == "vote":
            return {"type": "vote-cast", "vote": "yes"}
        if kind == "president-discard":
            drawn = g.drawn or ["fascist"]
            pol = "fascist" if "fascist" in drawn and st.role == "liberal" else drawn[0]
            return {"type": "president-discarded", "policy": pol}
        if kind == "chancellor-legislate":
            rec = g.received or ["fascist"]
            pol = "liberal" if "liberal" in rec and st.role == "liberal" else rec[0]
            return {"type": "policy-enacted", "policy": pol}
        if kind == "president-veto-response":
            return {"type": "veto-responded", "accepted": True}
        if kind == "investigate-player":
            cands = [s for s in st.others_alive() if s not in st.investigated] or st.others_alive()
            return {"type": "loyalty-investigated", "target": next_from(st.me, cands)}
        if kind == "special-election":
            return {"type": "special-election-called", "target": next_from(st.me, st.others_alive())}
        if kind == "execute-player":
            return {"type": "player-executed", "target": next_from(st.me, st.others_alive())}
        return {"type": "discussion-contributed", "statement": None}
