"""Role-specific decision policy for Secret Dictator.

Liberal: trust-weighted governments from the Bayesian belief; never risk a
Dictator chancellor after three fascist policies; always pass/enact Liberal.
Fascist: knows the team; builds trust early, pushes fascist policies when
cover exists, and tries to get the Dictator elected after three fascist policies.
Dictator: plays like a model liberal to collect trust, then aims for chancellor.
"""
import json
import os
import random

# Tunable behaviour of the fascist team (overridable via SD_PARAMS for experiments).
PARAMS = {
    "f_nominate_mate": 0.75,     # nominate a teammate when the table trusts them enough
    "f_mate_psus_max": 0.55,
    "f_vote_libgov_yes": 0.9,    # yes on a trusted-looking all-liberal government
    "f_vote_mate_yes": 0.6,      # yes on a government containing a teammate (bloc voting gives us away)
    "f_vote_tracker2_no": 0.6,
    "f_pres_pass_ff": 0.8,       # with a liberal chancellor, pass FF when possible
    "f_chanc_F_early": 0.55,     # mixed hand as chancellor, liberal president, <=1 F enacted
    "f_chanc_F": 0.7,
    "d_pres_discard_F_early": 0.8,
    "d_chanc_F_early": 0.3,
    "d_chanc_F_late": 0.55,
}
if os.environ.get("SD_PARAMS"):
    PARAMS.update(json.loads(os.environ["SD_PARAMS"]))


class Brain:
    def __init__(self, st, mine, public, rng=None):
        self.st = st
        self.mine = mine        # belief from my perspective
        self.public = public    # belief of a neutral observer (how the table sees us)
        self.rng = rng or random.Random()
        self.team = st.team_known()
        self.role = st.role

    # -- helpers ---------------------------------------------------------
    def sus(self, seat):
        """My estimate that `seat` is on the fascist team."""
        if seat == self.st.me:
            return 0.0 if self.role == "liberal" else 1.0
        if self.role == "fascist":
            return 1.0 if seat in self.team else 0.0
        return self.mine.p_team(seat)

    def pdict(self, seat):
        if seat == self.st.me:
            return 1.0 if self.role == "dictator" else 0.0
        if self.role == "fascist":
            return 1.0 if seat == self.st.known_dictator else 0.0
        if seat in self.st.not_dictator:
            return 0.0
        return self.mine.p_dictator(seat)

    def psus(self, seat):
        """How suspicious `seat` looks to the table."""
        return self.public.p_team(seat)

    def bad_gov(self, p, c):
        """P(government contains a fascist-team member), my perspective."""
        if self.role == "fascist":
            return 1.0 if (p in self.team or c in self.team) else 0.0
        seats = [s for s in (p, c) if s != self.st.me]
        if not seats:
            return 0.0
        if self.role == "dictator":
            return self.mine.p_any_team(seats)
        return self.mine.p_any_team(seats)

    def ref_bad(self):
        """Average badness over plausible alternative governments (for vote calibration)."""
        others = self.st.others_alive()
        vals = []
        for i, p in enumerate(self.st.alive):
            for c in self.st.alive:
                if c == p:
                    continue
                vals.append(self.bad_gov(p, c))
        return sum(vals) / len(vals) if vals else 0.5

    # -- nominations -----------------------------------------------------
    def nominate(self):
        st = self.st
        elig = st.eligible_chancellors(st.me)
        if not elig:
            elig = st.others_alive()
        if self.role == "fascist":
            d = st.known_dictator
            if st.fascist >= 3 and d in elig and d not in st.not_dictator:
                return d
            mates = [s for s in elig if s in self.team]
            # Nominate a teammate when the table doesn't distrust them much.
            mates.sort(key=self.psus)
            if mates and self.psus(mates[0]) < PARAMS["f_mate_psus_max"] and not (st.fascist >= 3 and mates[0] == d):
                if self.rng.random() < PARAMS["f_nominate_mate"]:
                    return mates[0]
            libs = [s for s in elig if s not in self.team]
            return min(libs or elig, key=self.psus)
        if self.role == "dictator":
            if st.fascist >= 3:
                # Want fascist policies: pick who I believe is most likely a teammate,
                # but only if they don't look terrible to the table.
                cand = sorted(elig, key=lambda s: -self.mine.p_team(s))
                for s in cand:
                    if self.psus(s) < 0.6:
                        return s
            return min(elig, key=lambda s: (self.psus(s), s))
        # Liberal: most trusted; after 3 fascist policies weigh dictator risk heavily.
        w = 3.0 if st.fascist >= 3 else 0.5
        return min(elig, key=lambda s: (self.sus(s) + w * self.pdict(s), s))

    def special_election(self):
        st = self.st
        cands = st.others_alive()
        if self.role == "fascist":
            mates = [s for s in cands if s in self.team and s != st.known_dictator]
            if mates:
                return mates[0]
            if st.known_dictator in cands:
                return st.known_dictator
            return min(cands, key=self.psus)
        if self.role == "dictator":
            return min(cands, key=lambda s: (self.psus(s), s))
        return min(cands, key=lambda s: (self.sus(s), s))

    # -- voting ----------------------------------------------------------
    def vote(self, p, c):
        st = self.st
        me = st.me
        if self.role == "fascist":
            d = st.known_dictator
            if st.fascist >= 3 and c == d:
                return "yes"
            if p in self.team or c in self.team:
                return "yes" if self.rng.random() < PARAMS["f_vote_mate_yes"] else "no"
            # All-liberal government: blend in, but chaos favours us.
            looks_good = self.psus(p) + self.psus(c) < 0.6
            if st.tracker >= 2:
                return "no" if self.rng.random() < PARAMS["f_vote_tracker2_no"] else "yes"
            return "yes" if looks_good and self.rng.random() < PARAMS["f_vote_libgov_yes"] else "no"
        if me in (p, c):
            if self.role == "dictator" or self.role == "liberal":
                return "yes"
        if self.role == "dictator":
            if st.fascist >= 3 and c == me:
                return "yes"
            # Mostly vote like a careful liberal (to look like one).
        bad = self.bad_gov(p, c)
        ref = self.ref_bad()
        if self.role == "liberal" and st.fascist >= 3:
            if self.pdict(c) > 0.12:
                return "no"
        if self.role == "liberal" and st.fascist >= 5 and bad > 0.25 and st.tracker < 2:
            return "no"
        margin = 0.06
        if st.tracker >= 2:
            margin = 0.25  # failing now enacts a random (likely fascist) policy
        if st.liberal >= 4 and self.role == "liberal":
            margin += 0.1  # one more liberal policy wins
        return "yes" if bad <= ref + margin else "no"

    # -- legislation -----------------------------------------------------
    def president_discard(self, drawn, chancellor):
        st = self.st
        has_l = "liberal" in drawn
        has_f = "fascist" in drawn
        if not (has_l and has_f):
            return drawn[0]
        if self.role == "liberal":
            return "fascist"
        mate = chancellor in self.team
        if self.role == "fascist":
            if st.fascist >= 5 or st.liberal >= 4:
                return "liberal"          # pass as many fascist cards as possible
            if mate:
                return "liberal"
            # Liberal chancellor: pass FF if possible (claim FFF), else LF either way.
            if drawn.count("fascist") >= 2:
                return "liberal" if self.rng.random() < PARAMS["f_pres_pass_ff"] else "fascist"
            return "liberal"              # LLF: pass LF (chancellor may still choose L)
        # Dictator: look liberal until three fascist policies, then lean fascist.
        if st.liberal >= 4:
            return "liberal"
        if st.fascist >= 3 and self.psus(st.me) < 0.5:
            return "liberal" if self.rng.random() < 0.6 else "fascist"
        return "fascist" if self.rng.random() < PARAMS["d_pres_discard_F_early"] else "liberal"

    def chancellor_enact(self, received, president, veto_allowed):
        """Returns 'liberal' / 'fascist' / 'veto'."""
        st = self.st
        has_l = "liberal" in received
        has_f = "fascist" in received
        if self.role == "liberal":
            if has_l:
                return "liberal"
            if veto_allowed:
                return "veto"
            return "fascist"
        if not has_f:
            return "liberal"
        if not has_l:
            return "fascist"
        # Mixed hand.
        if st.fascist >= 5 or st.liberal >= 4:
            return "fascist"              # win now / stop the liberal win
        if self.role == "fascist":
            if president in self.team:
                return "fascist"
            if st.fascist <= 1 and st.liberal <= 1:
                return "fascist" if self.rng.random() < PARAMS["f_chanc_F_early"] else "liberal"
            return "fascist" if self.rng.random() < PARAMS["f_chanc_F"] else "liberal"
        # Dictator: trust matters more than one policy until fascists are at 3.
        if st.fascist < 3:
            return "fascist" if self.rng.random() < PARAMS["d_chanc_F_early"] else "liberal"
        return "fascist" if self.rng.random() < PARAMS["d_chanc_F_late"] else "liberal"

    def veto_response(self, passed_l, chancellor):
        """President's answer to a veto request. passed_l = liberals I passed (or None)."""
        st = self.st
        if self.role == "liberal":
            if passed_l == 0:
                return True
            return False if passed_l else st.tracker < 2
        return False  # team: keep the fascist card on the table

    # -- executive powers -------------------------------------------------
    def investigate(self):
        st = self.st
        cands = [s for s in st.others_alive() if s not in st.investigated]
        if not cands:
            cands = st.others_alive()
        if self.role == "fascist":
            d = st.known_dictator
            if d in cands and self.psus(d) > 0.25:
                return d
            libs = [s for s in cands if s not in self.team]
            if libs:
                return min(libs, key=self.psus)  # frame the most trusted liberal
            return cands[0]
        # Most uncertain player, weighted toward those likely to hold office.
        def score(s):
            p = self.sus(s)
            return -(p * (1 - p)) - 0.05 * (1 - self.psus(s))
        return min(cands, key=lambda s: (score(s), s))

    def investigation_claim(self, target, faction):
        """What we announce after investigating."""
        if self.role == "liberal":
            return faction
        if self.role == "fascist":
            if target in self.team:
                return "liberal"
            return "fascist" if self.rng.random() < 0.6 else "liberal"
        # Dictator: truthful unless the result would expose a likely teammate... (unknown) -> truth
        return faction

    def execute(self):
        st = self.st
        cands = st.others_alive()
        if self.role == "fascist":
            libs = [s for s in cands if s not in self.team]
            # Kill the liberal the table trusts most (likely to expose us / be elected).
            return min(libs or cands, key=lambda s: (self.psus(s), s))
        if self.role == "dictator":
            # Kill whoever I think is most liberal, but avoid looking odd: pick a moderately
            # suspected player I believe is liberal.
            return max(cands, key=lambda s: ((1 - self.mine.p_team(s)) + self.psus(s), s))
        return max(cands, key=lambda s: (2.0 * self.pdict(s) + self.sus(s), s))
