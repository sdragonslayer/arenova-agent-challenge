"""Secret Dictator state reconstruction from the visible history."""

POWERS_7P = {2: "investigate", 3: "special-election", 4: "execute", 5: "execute"}


class Gov:
    """One nomination attempt (and, if elected, its legislative session)."""

    def __init__(self, rnd, president):
        self.round = rnd
        self.president = president
        self.chancellor = None
        self.eligible = []          # chancellor candidates at nomination time
        self.votes = {}
        self.passed = None
        self.elected = False
        self.enacted = None         # "liberal" / "fascist" / None
        self.vetoed = False
        self.veto_requested = False
        self.veto_rejected = False
        self.drawn = None           # my private view (if president)
        self.discarded = None
        self.received = None        # my private view (if chancellor)
        self.pile_size = None       # draw pile before the president drew
        self.pile_pl = None         # estimated P(liberal) per card in the pile
        self.fascist_before = 0
        self.liberal_before = 0
        self.claims = {}            # seat -> dict (filled by the claim parser)
        self.power = None

    def pair(self):
        return (self.president, self.chancellor)


class SDState:
    def __init__(self, history):
        items = sorted(history, key=lambda it: it.get("seq", 0))
        self.items = items
        start = None
        for it in items:
            ev = it.get("event") or {}
            if ev.get("type") == "game-started":
                start = ev
                break
        if start is None:
            raise ValueError("no game-started")
        you = start.get("you") or {}
        self.me = you.get("seat")
        self.role = you.get("role")
        self.faction = you.get("faction")
        self.known_fascists = list(start.get("knownFascists") or [])
        self.known_dictator = start.get("knownDictator")
        self.seats = list(start.get("seats") or ["seat-%d" % i for i in range(1, 8)])
        self.alive = list(self.seats)
        self.round = int(start.get("round") or 1)
        self.president = start.get("president")
        self.liberal = 0
        self.fascist = 0
        self.tracker = 0
        self.last_president = None   # term limits (last elected government)
        self.last_chancellor = None
        self.govs = []
        self.cur = None
        self.investigated = set()
        self.investigations = []      # (round, investigator, target, faction or None)
        self.executions = []          # (round, president, target)
        self.special_elections = []   # (round, president, candidate)
        self.not_dictator = set()
        self.statements = []          # (seq, from, context, text)
        self.passes = []
        self.pending_power = None
        self.winner = None
        self.chaos_count = 0
        # Deck tracking (cycle = since last reshuffle).
        self.pile = 17
        self.cycle_l, self.cycle_f = 6, 11
        self.cycle_seen_l = 0       # liberal cards known to have left the pile this cycle
        self.cycle_seen_f = 0
        self.cycle_unknown = 0      # cards drawn this cycle whose identity we don't know
        self.awaiting_chaos = False
        for it in items:
            self._apply(it.get("seq", 0), it.get("event") or {})

    # ------------------------------------------------------------------
    def _gov(self, rnd=None):
        if self.cur is None or (rnd is not None and self.cur.round != rnd and self.cur.enacted is not None):
            self.cur = Gov(rnd if rnd is not None else self.round, self.president)
            self.govs.append(self.cur)
        return self.cur

    def _apply(self, seq, ev):
        t = ev.get("type")
        if t == "president-announced":
            self.president = ev.get("president")
            self.round = int(ev.get("round") or self.round)
            self.cur = Gov(self.round, self.president)
            self.govs.append(self.cur)
        elif t == "chancellor-nominated":
            g = self._gov(ev.get("round"))
            g.president = ev.get("president") or g.president
            g.chancellor = ev.get("chancellor")
            g.eligible = self.eligible_chancellors(g.president)
            g.fascist_before, g.liberal_before = self.fascist, self.liberal
        elif t == "votes-revealed":
            g = self._gov(ev.get("round"))
            g.votes = dict(ev.get("votes") or {})
            g.passed = bool(ev.get("passed"))
        elif t == "government-elected":
            g = self._gov(ev.get("round"))
            g.elected = True
            g.president = ev.get("president") or g.president
            g.chancellor = ev.get("chancellor") or g.chancellor
            self.last_president = g.president
            self.last_chancellor = g.chancellor
            self.tracker = 0
        elif t == "chancellor-confirmed-not-dictator":
            self.not_dictator.add(ev.get("seat"))
        elif t == "election-tracker-advanced":
            self.tracker = int(ev.get("electionTracker") or 0)
        elif t == "chaos-triggered":
            self.awaiting_chaos = True
            self.chaos_count += 1
        elif t == "deck-reshuffled":
            self.pile = 17 - self.liberal - self.fascist
            self.cycle_l = 6 - self.liberal
            self.cycle_f = 11 - self.fascist
            self.cycle_seen_l = self.cycle_seen_f = self.cycle_unknown = 0
        elif t == "president-cards-drawn":
            g = self._gov(ev.get("round"))
            g.pile_size = self.pile
            g.pile_pl = self.pile_pl()
            g.fascist_before, g.liberal_before = self.fascist, self.liberal
            pol = ev.get("policies")
            if pol and ev.get("president") == self.me:
                g.drawn = list(pol)
            self.pile -= 3
            if pol:
                self.cycle_seen_l += pol.count("liberal")
                self.cycle_seen_f += pol.count("fascist")
            else:
                self.cycle_unknown += 3
        elif t == "president-policy-discarded":
            g = self._gov(ev.get("round"))
            if ev.get("policy") and ev.get("president") == self.me:
                g.discarded = ev.get("policy")
        elif t == "chancellor-policies-received":
            g = self._gov(ev.get("round"))
            pol = ev.get("policies")
            if pol and ev.get("chancellor") == self.me:
                g.received = list(pol)
                if g.drawn is None:
                    # Chancellor saw two of the three drawn cards.
                    self.cycle_seen_l += pol.count("liberal")
                    self.cycle_seen_f += pol.count("fascist")
                    self.cycle_unknown -= 2
        elif t == "policy-enacted":
            pol = ev.get("policy")
            if ev.get("source") == "chaos":
                self.pile -= 1
                if pol == "liberal":
                    self.cycle_seen_l += 1
                else:
                    self.cycle_seen_f += 1
                self.last_president = None   # chaos resets term limits
                self.last_chancellor = None
                self.tracker = 0
                self.awaiting_chaos = False
            else:
                g = self._gov(ev.get("round"))
                g.enacted = pol
                if g.drawn is None and g.received is None:
                    # One of the unknown drawn cards is now known.
                    if pol == "liberal":
                        self.cycle_seen_l += 1
                    else:
                        self.cycle_seen_f += 1
                    self.cycle_unknown -= 1
            if pol == "liberal":
                self.liberal += 1
            elif pol == "fascist":
                self.fascist += 1
        elif t == "veto-requested":
            g = self._gov(ev.get("round"))
            g.veto_requested = True
        elif t == "veto-responded":
            g = self._gov(ev.get("round"))
            if ev.get("accepted"):
                g.vetoed = True
            else:
                g.veto_rejected = True
        elif t == "executive-power-required":
            self.pending_power = ev.get("power")
            if self.cur is not None:
                self.cur.power = ev.get("power")
        elif t == "loyalty-investigated":
            self.investigated.add(ev.get("target"))
            self.pending_power = None
        elif t == "investigation-result":
            self.investigated.add(ev.get("target"))
            self.investigations.append((ev.get("round"), ev.get("investigator"), ev.get("target"), ev.get("faction")))
        elif t == "special-election-called":
            self.special_elections.append((ev.get("round"), ev.get("president"), ev.get("presidentCandidate")))
            self.pending_power = None
        elif t == "player-executed":
            tgt = ev.get("target")
            self.executions.append((ev.get("round"), ev.get("president"), tgt))
            if tgt in self.alive:
                self.alive.remove(tgt)
            self.not_dictator.add(tgt)  # the game would have ended otherwise
            self.pending_power = None
        elif t == "player-statement":
            self.statements.append((seq, ev.get("from"), ev.get("context") or "", ev.get("text") or ""))
        elif t == "speaking-slot-passed":
            self.passes.append((seq, ev.get("seat"), ev.get("context")))
        elif t == "game-ended":
            self.winner = ev.get("winner")

    # ------------------------------------------------------------------
    def pile_pl(self):
        """P(a card in the draw pile is liberal), treating unknown discards as random."""
        unknown_pool = self.cycle_l + self.cycle_f - self.cycle_seen_l - self.cycle_seen_f
        unknown_l = self.cycle_l - self.cycle_seen_l
        if unknown_pool <= 0:
            return 6.0 / 17.0
        return max(0.0, min(1.0, unknown_l / float(unknown_pool)))

    def eligible_chancellors(self, president=None):
        president = president or self.president
        out = []
        n_alive = len(self.alive)
        for s in self.alive:
            if s == president:
                continue
            if s == self.last_chancellor:
                continue
            if n_alive > 5 and s == self.last_president:
                continue
            out.append(s)
        return out

    def others_alive(self):
        return [s for s in self.alive if s != self.me]

    def gov_by_round(self, rnd):
        for g in reversed(self.govs):
            if g.round == rnd and g.elected:
                return g
        return None

    def current_gov(self):
        return self.cur

    def team_known(self):
        """Seats known to be on the fascist team (from my perspective)."""
        if self.role == "fascist":
            out = set(self.known_fascists) | {self.me}
            if self.known_dictator:
                out.add(self.known_dictator)
            return out
        if self.role == "dictator":
            return {self.me}
        return set()

    @property
    def veto_unlocked(self):
        return self.fascist >= 5
