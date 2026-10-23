"""Local 7-player Secret Dictator engine (event-sourced, per-seat visibility).

Usage: python tools/sim_sd.py -n 50 --bots ours,simple,simple,simple,simple,simple,simple [--seed 1] [-v]
Bot specs: ours (in-process), proc (src/main.py subprocess), simple (in-process baseline), random.
Roles are shuffled every game; results are reported per bot type and role.
"""
import argparse
import copy
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proc import make_bot, InProcBot  # noqa: E402

SEATS = ["seat-%d" % i for i in range(1, 8)]
POWERS = {2: "investigate", 3: "special-election", 4: "execute", 5: "execute"}


class Fault(Exception):
    pass


# ----------------------------------------------------------------------
class SimpleBot:
    """Baseline: honest liberal / greedy fascist with templated claims."""

    def __init__(self, seat, rnd=False):
        self.seat = seat
        self.rng = random.Random()
        self.rnd = rnd

    def decide(self, msg, budget=None):
        hist = [it["event"] for it in msg["history"]]
        start = hist[0]
        me = start["you"]["seat"]
        role = start["you"]["role"]
        bad = role != "liberal"
        team = set(start.get("knownFascists") or []) | ({start.get("knownDictator")} - {None}) | {me}
        kind = msg["decision"]["kind"]
        alive = [s for s in start["seats"]]
        last_p = last_c = None
        investigated = set()
        drawn = received = None
        nominee = president = None
        fascist = 0
        for e in hist:
            t = e["type"]
            if t == "player-executed":
                alive.remove(e["target"])
            elif t == "government-elected":
                last_p, last_c = e["president"], e["chancellor"]
            elif t == "policy-enacted":
                if e["policy"] == "fascist":
                    fascist += 1
                if e.get("source") == "chaos":
                    last_p = last_c = None
            elif t == "president-announced":
                president = e["president"]
            elif t == "chancellor-nominated":
                nominee = e["chancellor"]
            elif t == "president-cards-drawn" and e.get("policies"):
                drawn = e["policies"]
            elif t == "chancellor-policies-received" and e.get("policies"):
                received = e["policies"]
            elif t == "loyalty-investigated":
                investigated.add(e["target"])
        others = [s for s in alive if s != me]
        r = self.rng
        if kind == "nominate-chancellor":
            elig = [s for s in others if s != last_c and not (len(alive) > 5 and s == last_p)]
            mates = [s for s in elig if s in team]
            t = r.choice(mates) if (bad and mates and r.random() < 0.7) else r.choice(elig)
            return {"type": "chancellor-nominated", "target": t, "statement": "I nominate %s." % t}
        if kind == "vote":
            if bad and (nominee in team or president in team):
                return {"type": "vote-cast", "vote": "yes"}
            if bad or self.rnd:
                return {"type": "vote-cast", "vote": "yes" if r.random() < 0.7 else "no"}
            # Liberal heuristic: distrust anyone who sat in a government that enacted Fascist;
            # after 3 Fascist policies only elect chancellors who previously enacted Liberal.
            tainted, proven = set(), set()
            cur_p = cur_c = None
            for e in hist:
                if e["type"] == "government-elected":
                    cur_p, cur_c = e["president"], e["chancellor"]
                elif e["type"] == "policy-enacted" and e.get("source") == "government":
                    if e["policy"] == "fascist":
                        tainted |= {cur_p, cur_c}
                    else:
                        proven.add(cur_c)
            if nominee in tainted or president in tainted:
                return {"type": "vote-cast", "vote": "no"}
            if fascist >= 3 and nominee not in proven:
                return {"type": "vote-cast", "vote": "no"}
            return {"type": "vote-cast", "vote": "yes" if r.random() < 0.85 else "no"}
        if kind == "president-discard":
            if self.rnd:
                return {"type": "president-discarded", "policy": r.choice(drawn)}
            pol = "liberal" if bad and "liberal" in drawn else ("fascist" if "fascist" in drawn else "liberal")
            return {"type": "president-discarded", "policy": pol}
        if kind == "chancellor-legislate":
            if self.rnd:
                return {"type": "policy-enacted", "policy": r.choice(received)}
            pol = "fascist" if bad and "fascist" in received else ("liberal" if "liberal" in received else "fascist")
            return {"type": "policy-enacted", "policy": pol}
        if kind == "president-veto-response":
            return {"type": "veto-responded", "accepted": not bad}
        if kind == "investigate-player":
            c = [s for s in others if s not in investigated] or others
            return {"type": "loyalty-investigated", "target": r.choice(c)}
        if kind == "special-election":
            return {"type": "special-election-called", "target": r.choice(others)}
        if kind == "execute-player":
            c = [s for s in others if not (bad and s in team)] or others
            return {"type": "player-executed", "target": r.choice(c)}
        if kind == "discuss":
            # Claim once as government member.
            gov = None
            for e in reversed(hist):
                if e["type"] == "policy-enacted" and e.get("source") == "government":
                    gov = e
                    break
            said = any(e["type"] == "player-statement" and e["from"] == me and e["context"].endswith(":%s" % (gov or {}).get("round")) for e in hist)
            if gov and not said:
                if president == me and drawn:
                    k = drawn.count("liberal")
                    if bad and gov["policy"] == "fascist":
                        k = 0
                    return {"type": "discussion-contributed",
                            "statement": "I drew %s." % ("F" * (3 - k) + "L" * k)}
                if gov.get("chancellor") == me and received:
                    j = received.count("liberal")
                    if bad and gov["policy"] == "fascist":
                        j = 0
                    return {"type": "discussion-contributed",
                            "statement": "I received %s." % ("F" * (2 - j) + "L" * j)}
            return {"type": "discussion-contributed", "statement": None}
        raise ValueError(kind)

    def fallback(self, msg):
        return self.decide(msg)


# ----------------------------------------------------------------------
class SDGame:
    def __init__(self, specs, seed=None, verbose=False, deadline=65.0):
        self.rng = random.Random(seed)
        self.verbose = verbose
        self.deadline = deadline
        roles = ["liberal"] * 4 + ["fascist", "fascist", "dictator"]
        self.rng.shuffle(roles)
        self.roles = dict(zip(SEATS, roles))
        self.specs = dict(zip(SEATS, specs))
        self.deck = ["liberal"] * 6 + ["fascist"] * 11
        self.rng.shuffle(self.deck)
        self.discard = []
        self.alive = list(SEATS)
        self.L = self.F = 0
        self.tracker = 0
        self.last_p = self.last_c = None
        self.log = []
        self.winner = None
        self.reason = None
        self.round = 0
        self.investigated = set()
        self.president_idx = self.rng.randrange(7)
        self.bots = {}
        self.faults = {s: 0 for s in SEATS}
        self.stats = {s: {"n": 0, "t": 0.0, "max": 0.0} for s in SEATS}
        for s in SEATS:
            spec = self.specs[s]
            if spec == "simple":
                self.bots[s] = InProcBot(lambda seat: SimpleBot(seat), s, name="simple")
            elif spec == "random":
                self.bots[s] = InProcBot(lambda seat: SimpleBot(seat, rnd=True), s, name="random")
            else:
                self.bots[s] = make_bot(spec, s, lambda: __import__("sd.agent", fromlist=["Agent"]).Agent, soft=40.0)

    # -- events --------------------------------------------------------
    def emit(self, ev, private=None):
        self.log.append((ev, private or {}))
        if self.verbose:
            shown = ev
            if private:
                shown = dict(ev, _private={k: {kk: vv for kk, vv in v.items() if kk not in ev} for k, v in private.items()})
            print("   ", shown)

    def history(self, seat):
        return [{"seq": i, "atMs": i * 10, "event": copy.deepcopy(p.get(seat, ev))} for i, (ev, p) in enumerate(self.log)]

    def ask(self, seat, kind, rid):
        msg = {"type": "decision", "decision": {"requestId": rid, "seat": seat, "kind": kind},
               "history": self.history(seat), "deadlineMs": int(self.deadline * 1000)}
        r, dt = self.bots[seat].request(msg, timeout=self.deadline)
        st = self.stats[seat]
        st["n"] += 1
        st["t"] += dt
        st["max"] = max(st["max"], dt)
        if r is None or dt > self.deadline:
            raise Fault("%s no reply for %s" % (seat, kind))
        return r

    def ask_valid(self, seat, kind, rid, check, baseline):
        """Ask; on invalid reply record a fault and use the baseline (or abort in round 1)."""
        try:
            r = self.ask(seat, kind, rid)
            if not isinstance(r, dict) or not check(r):
                raise Fault("%s invalid %s: %r" % (seat, kind, r))
            return r
        except Fault as f:
            self.faults[seat] += 1
            print("  FAULT", self.specs[seat], self.roles[seat], f)
            if self.round <= 1:
                raise AbortMatch(str(f))
            return baseline()

    def statement(self, seat, r, context):
        txt = r.get("statement")
        if txt is not None:
            if not isinstance(txt, str) or not txt.strip() or len(txt) > 320:
                raise Fault("bad statement %r" % (txt,))
            self.emit({"type": "player-statement", "from": seat, "context": context, "text": txt})

    def next_legal(self, me, cands):
        m = int(me.split("-")[1])
        return sorted(cands, key=lambda s: (int(s.split("-")[1]) - m - 1) % 7)[0]

    # -- game ----------------------------------------------------------
    def start(self):
        for s in SEATS:
            self.bots[s].request({"type": "init", "game": "secret-dictator", "seat": s}, timeout=15)
        fas = [s for s in SEATS if self.roles[s] == "fascist"]
        dic = [s for s in SEATS if self.roles[s] == "dictator"][0]
        pub = {"type": "game-started", "rulesProfile": "secret-dictator-7p-event-sourced-v1",
               "seats": list(SEATS), "president": SEATS[self.president_idx], "round": 1}
        priv = {}
        for s in SEATS:
            r = self.roles[s]
            e = dict(pub, you={"seat": s, "role": r, "faction": "liberal" if r == "liberal" else "fascist"})
            if r == "fascist":
                e["knownFascists"] = [x for x in fas if x != s]
                e["knownDictator"] = dic
            else:
                e["knownFascists"] = []
                e["knownDictator"] = None
            priv[s] = e
        self.emit(pub, priv)

    def draw(self, n):
        if len(self.deck) < 3:
            self.deck += self.discard
            self.discard = []
            self.rng.shuffle(self.deck)
            self.emit({"type": "deck-reshuffled"})
        out, self.deck = self.deck[:n], self.deck[n:]
        return out

    def enact(self, pol, source, chancellor=None):
        ev = {"type": "policy-enacted", "round": self.round, "policy": pol, "source": source}
        if chancellor:
            ev["chancellor"] = chancellor
        self.emit(ev)
        if pol == "liberal":
            self.L += 1
        else:
            self.F += 1
        if self.L >= 5:
            return self.end("liberal", "five-liberal-policies")
        if self.F >= 6:
            return self.end("fascist", "six-fascist-policies")

    def end(self, winner, reason):
        self.winner, self.reason = winner, reason
        self.emit({"type": "game-ended", "winner": winner, "reason": reason})
        return True

    def advance_tracker(self):
        self.tracker += 1
        self.emit({"type": "election-tracker-advanced", "electionTracker": self.tracker})
        if self.tracker >= 3:
            self.emit({"type": "chaos-triggered"})
            pol = self.draw(1)[0]
            self.tracker = 0
            self.last_p = self.last_c = None
            self.enact(pol, "chaos")

    def discussion(self, p, c):
        ctx = "government-aftermath:%d" % self.round
        order = [p, c]
        i = SEATS.index(p)
        for k in range(1, 8):
            s = SEATS[(i + k) % 7]
            if s in self.alive and s not in order:
                order.append(s)
        self.emit({"type": "discussion-started", "round": self.round, "context": ctx, "tableOrder": order})
        for dr, speakers in ((1, order), (2, order), (3, [p, c])):
            for s in speakers:
                rid = "round:%d:discuss:%d:%s" % (self.round, dr, s)
                r = self.ask_valid(s, "discuss", rid, lambda r: r.get("type") == "discussion-contributed"
                                   and (r.get("statement") is None or (isinstance(r["statement"], str) and 0 < len(r["statement"].strip()) and len(r["statement"]) <= 320)),
                                   lambda: {"type": "discussion-contributed", "statement": None})
                if r.get("statement"):
                    self.emit({"type": "player-statement", "from": s, "context": ctx, "text": r["statement"]})
                else:
                    self.emit({"type": "speaking-slot-passed", "round": self.round, "seat": s, "context": ctx, "discussion-round": dr})
        self.emit({"type": "discussion-ended", "round": self.round, "context": ctx})

    def play_round(self, president):
        self.round += 1
        rnd = self.round
        self.emit({"type": "president-announced", "president": president, "round": rnd})
        elig = [s for s in self.alive if s != president and s != self.last_c
                and not (len(self.alive) > 5 and s == self.last_p)]
        r = self.ask_valid(president, "nominate-chancellor", "round:%d:nominate-chancellor:%s" % (rnd, president),
                           lambda r: r.get("type") == "chancellor-nominated" and r.get("target") in elig
                           and (r.get("statement") is None or (isinstance(r["statement"], str) and 0 < len(r["statement"]) <= 320)),
                           lambda: {"type": "chancellor-nominated", "target": self.next_legal(president, elig)})
        c = r["target"]
        self.emit({"type": "chancellor-nominated", "round": rnd, "president": president, "chancellor": c})
        if r.get("statement"):
            self.emit({"type": "player-statement", "from": president, "context": "nomination:%d" % rnd, "text": r["statement"]})
        votes = {}
        for s in self.alive:
            v = self.ask_valid(s, "vote", "round:%d:vote:%s" % (rnd, s),
                               lambda r: r.get("type") == "vote-cast" and r.get("vote") in ("yes", "no"),
                               lambda: {"type": "vote-cast", "vote": "yes"})
            votes[s] = v["vote"]
        passed = sum(1 for v in votes.values() if v == "yes") * 2 > len(self.alive)
        self.emit({"type": "votes-revealed", "round": rnd, "votes": votes, "passed": passed})
        if not passed:
            self.advance_tracker()
            return
        if self.F >= 3:
            if self.roles[c] == "dictator":
                return self.end("fascist", "dictator-elected-chancellor")
            self.emit({"type": "chancellor-confirmed-not-dictator", "round": rnd, "seat": c})
        self.emit({"type": "government-elected", "round": rnd, "president": president, "chancellor": c})
        self.last_p, self.last_c = president, c
        self.tracker = 0
        hand = self.draw(3)
        self.emit({"type": "president-cards-drawn", "round": rnd, "president": president},
                  {president: {"type": "president-cards-drawn", "round": rnd, "president": president, "policies": list(hand)}})
        d = self.ask_valid(president, "president-discard", "round:%d:president-discard" % rnd,
                           lambda r: r.get("type") == "president-discarded" and r.get("policy") in hand,
                           lambda: {"type": "president-discarded", "policy": self.rng.choice(hand)})
        hand.remove(d["policy"])
        self.discard.append(d["policy"])
        self.emit({"type": "president-policy-discarded", "round": rnd, "president": president},
                  {president: {"type": "president-policy-discarded", "round": rnd, "president": president, "policy": d["policy"]}})
        self.emit({"type": "chancellor-policies-received", "round": rnd, "chancellor": c},
                  {c: {"type": "chancellor-policies-received", "round": rnd, "chancellor": c, "policies": list(hand)}})
        veto_ok = self.F >= 5
        while True:
            lg = self.ask_valid(c, "chancellor-legislate", "round:%d:chancellor-legislate" % rnd,
                                lambda r: (r.get("type") == "policy-enacted" and r.get("policy") in hand)
                                or (r.get("type") == "veto-requested" and veto_ok),
                                lambda: {"type": "policy-enacted", "policy": self.rng.choice(hand)})
            if lg["type"] == "veto-requested":
                self.emit({"type": "veto-requested", "round": rnd, "chancellor": c})
                vr = self.ask_valid(president, "president-veto-response", "round:%d:veto" % rnd,
                                    lambda r: r.get("type") == "veto-responded" and isinstance(r.get("accepted"), bool),
                                    lambda: {"type": "veto-responded", "accepted": True})
                self.emit({"type": "veto-responded", "round": rnd, "president": president, "accepted": vr["accepted"]})
                if vr["accepted"]:
                    self.discard += hand
                    self.discussion(president, c)
                    self.advance_tracker()
                    return
                veto_ok = False
                continue
            break
        pol = lg["policy"]
        hand.remove(pol)
        self.discard += hand
        if self.enact(pol, "government", c):
            return
        power = POWERS.get(self.F) if pol == "fascist" else None
        if power:
            self.emit({"type": "executive-power-required", "power": power})
        self.discussion(president, c)
        others = [s for s in self.alive if s != president]
        if power == "investigate":
            cands = [s for s in others if s not in self.investigated]
            r = self.ask_valid(president, "investigate-player", "round:%d:investigate" % rnd,
                               lambda r: r.get("type") == "loyalty-investigated" and r.get("target") in cands,
                               lambda: {"type": "loyalty-investigated", "target": self.next_legal(president, cands)})
            t = r["target"]
            self.investigated.add(t)
            self.emit({"type": "loyalty-investigated", "round": rnd, "president": president, "target": t})
            fac = "liberal" if self.roles[t] == "liberal" else "fascist"
            self.emit({"type": "investigation-result", "round": rnd, "investigator": president, "target": t},
                      {president: {"type": "investigation-result", "round": rnd, "investigator": president, "target": t, "faction": fac}})
        elif power == "special-election":
            r = self.ask_valid(president, "special-election", "round:%d:special" % rnd,
                               lambda r: r.get("type") == "special-election-called" and r.get("target") in others,
                               lambda: {"type": "special-election-called", "target": self.next_legal(president, others)})
            self.special = r["target"]
            ret = self.next_president_after(president)
            self.emit({"type": "special-election-called", "round": rnd, "president": president,
                       "presidentCandidate": r["target"], "returnSeat": ret})
            if r.get("statement"):
                self.emit({"type": "player-statement", "from": president, "context": "special-election:%d" % rnd, "text": r["statement"]})
            self.return_seat = ret
        elif power == "execute":
            r = self.ask_valid(president, "execute-player", "round:%d:execute" % rnd,
                               lambda r: r.get("type") == "player-executed" and r.get("target") in others,
                               lambda: {"type": "player-executed", "target": self.next_legal(president, others)})
            t = r["target"]
            self.alive.remove(t)
            self.emit({"type": "player-executed", "round": rnd, "president": president, "target": t})
            if self.roles[t] == "dictator":
                self.end("liberal", "dictator-executed")

    def next_president_after(self, seat):
        i = SEATS.index(seat)
        for k in range(1, 8):
            s = SEATS[(i + k) % 7]
            if s in self.alive:
                return s

    def run(self):
        self.start()
        pres = SEATS[self.president_idx]
        self.special = None
        self.return_seat = None
        try:
            while not self.reason and self.round < 60:
                self.play_round(pres)
                if self.special:
                    pres, self.special = self.special, None
                elif self.return_seat:
                    pres, self.return_seat = self.return_seat, None
                    if pres not in self.alive:
                        pres = self.next_president_after(pres)
                else:
                    pres = self.next_president_after(pres)
        except AbortMatch as e:
            self.reason = "aborted: " + str(e)
        for b in self.bots.values():
            b.close()
        return self.winner, self.reason


class AbortMatch(Exception):
    pass


def main():
    from lowprio import set_low_priority
    set_low_priority()
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--bots", default="ours,simple,simple,simple,simple,simple,simple")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()
    specs = args.bots.split(",")
    assert len(specs) == 7
    rng = random.Random(args.seed)
    table = {}   # (spec, role) -> [wins, games]
    reasons = {}
    t0 = time.time()
    maxdt = 0.0
    for gno in range(args.n):
        rot = gno % 7
        order = specs[rot:] + specs[:rot]
        game = SDGame(order, seed=rng.randrange(1 << 30), verbose=args.v)
        winner, reason = game.run()
        reasons[reason] = reasons.get(reason, 0) + 1
        for s in SEATS:
            role = game.roles[s]
            fac = "liberal" if role == "liberal" else "fascist"
            k = (game.specs[s], role)
            w = table.setdefault(k, [0, 0])
            w[1] += 1
            w[0] += 1 if winner == fac else 0
            if game.specs[s] == "ours":
                maxdt = max(maxdt, game.stats[s]["max"])
                for line in game.bots[s].stderr[-1:]:
                    print("   stderr:", line[-600:])
        print("game %d: winner=%s reason=%s rounds=%d" % (gno, winner, reason, game.round), flush=True)
    print("reasons:", reasons)
    for (spec, role), (w, n) in sorted(table.items()):
        print("  %-8s %-9s win %3d/%3d = %.2f" % (spec, role, w, n, w / n))
    print("max ours decision %.2fs, elapsed %.0fs" % (maxdt, time.time() - t0))


if __name__ == "__main__":
    main()
