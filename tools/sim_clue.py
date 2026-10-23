"""Local Clue engine reproducing the Crucible protocol as documented.

Usage: python tools/sim_clue.py -n 20 --bots ours,example,example [--seed 1] [-v]
Bot specs: ours (in-process agent), proc (src/main.py subprocess), example, or a script path.
"""
import argparse
import copy
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proc import make_bot, ROOT  # noqa: E402
import clue_board  # noqa: E402

SUSPECTS = ["scarlet", "mustard", "white", "green", "peacock", "plum"]
WEAPONS = ["rope", "lead-pipe", "knife", "wrench", "candlestick", "pistol"]
ROOMS = ["kitchen", "ballroom", "conservatory", "dining-room", "billiard-room", "library", "lounge", "hall", "study"]


class Fault(Exception):
    pass


class ClueGame:
    def __init__(self, bots_specs, seed=None, turn_limit=60, verbose=False, deadline=65.0):
        self.rng = random.Random(seed)
        self.verbose = verbose
        self.strict = False  # the real engine's stance on re-entering the same room is unknown
        self.deadline = deadline
        n = len(bots_specs)
        self.seats = ["seat-%d" % (i + 1) for i in range(n)]
        self.characters = {s: SUSPECTS[i] for i, s in enumerate(self.seats)}
        self.char_seat = {v: k for k, v in self.characters.items()}
        self.solution = {
            "suspect": self.rng.choice(SUSPECTS),
            "weapon": self.rng.choice(WEAPONS),
            "room": self.rng.choice(ROOMS),
        }
        deck = ["suspect:" + s for s in SUSPECTS if s != self.solution["suspect"]]
        deck += ["weapon:" + w for w in WEAPONS if w != self.solution["weapon"]]
        deck += ["room:" + r for r in ROOMS if r != self.solution["room"]]
        self.rng.shuffle(deck)
        self.hands = {s: [] for s in self.seats}
        for i, c in enumerate(deck):
            self.hands[self.seats[i % n]].append(c)
        self.board = clue_board.load()
        self.walk = {(r, c) for r, line in enumerate(self.board["rows"]) for c, ch in enumerate(line) if ch == "-"}
        self.starts = {(v["row"], v["column"]) for v in self.board["starts"].values()}
        self.doors = {}
        for d in self.board["doors"]:
            self.doors.setdefault((d["corridor"]["row"], d["corridor"]["column"]), set()).add(d["room"])
        self.passages = {}
        for p in self.board["secretPassages"]:
            self.passages[p["from"]] = p["to"]
            self.passages[p["to"]] = p["from"]
        self.pos = {s: {"kind": "corridor", **self.board["starts"][s]} for s in SUSPECTS}
        self.weapon_pos = {w: self.rng.choice(ROOMS) for w in WEAPONS}
        self.turn_limit = turn_limit
        self.turn = 1
        self.active = self.seats[0]
        self.roll = self.dice()
        self.eliminated = set()
        self.faulted = set()
        self.pulled = set()
        self.log = []   # (event, visibility) visibility: None = public, or dict seat->variant
        self.winner = None
        self.reason = None
        self.stats = {s: {"decisions": 0, "time": 0.0, "max": 0.0} for s in self.seats}
        loader = lambda: __import__("clue.agent", fromlist=["Agent"]).Agent
        self.bots = {}
        for s, spec in zip(self.seats, bots_specs):
            label = spec
            spec = os.path.join(ROOT, "tools", "bots", "clue_example.py") if spec == "example" else spec
            self.bots[s] = make_bot("ours" if spec == "ours-base" else spec, s, loader)
            if spec == "ours-base":
                self.bots[s].agent.threat_aware = False
            self.names = getattr(self, "names", {})
            self.names[s] = label if label in ("ours", "proc", "ours-base") else os.path.basename(spec)

    def dice(self):
        return [self.rng.randint(1, 6), self.rng.randint(1, 6)]

    # -- event log -----------------------------------------------------
    def emit(self, event, private=None):
        """private: dict seat -> full event; others get `event`."""
        self.log.append((event, private))
        if self.verbose:
            print("  ", (private and next(iter(private.values()))) or event)

    def history_for(self, seat):
        out = []
        for i, (ev, private) in enumerate(self.log):
            e = private.get(seat, ev) if private else ev
            out.append({"seq": i, "atMs": i * 10, "event": copy.deepcopy(e)})
        return out

    def ask(self, seat, kind):
        msg = {
            "type": "decision",
            "decision": {"requestId": "turn:%d:%s" % (self.turn, kind), "seat": seat, "kind": kind},
            "history": self.history_for(seat),
            "deadlineMs": int(self.deadline * 1000),
        }
        reply, dt = self.bots[seat].request(msg, timeout=self.deadline)
        st = self.stats[seat]
        st["decisions"] += 1
        st["time"] += dt
        st["max"] = max(st["max"], dt)
        if reply is None or dt > self.deadline:
            raise Fault("no reply / late (%.1fs)" % dt)
        return reply

    # -- setup ---------------------------------------------------------
    def start(self):
        for s, bot in self.bots.items():
            bot.request({"type": "init", "game": "clue", "seat": s}, timeout=15)
        public = {
            "type": "game-started",
            "seats": list(self.seats), "turnOrder": list(self.seats),
            "characters": dict(self.characters),
            "handSizes": {s: len(h) for s, h in self.hands.items()},
            "suspectPositions": copy.deepcopy(self.pos),
            "weaponPositions": dict(self.weapon_pos),
            "activeSeat": self.active, "turn": self.turn, "roll": list(self.roll),
            "suspects": SUSPECTS, "weapons": WEAPONS, "rooms": ROOMS,
            "cards": {"suspects": ["suspect:" + s for s in SUSPECTS],
                      "weapons": ["weapon:" + w for w in WEAPONS],
                      "rooms": ["room:" + r for r in ROOMS]},
            "board": self.board, "turnLimit": self.turn_limit,
        }
        private = {}
        for s in self.seats:
            e = dict(public)
            e["you"] = {"seat": s, "character": self.characters[s]}
            e["hand"] = list(self.hands[s])
            private[s] = e
        self.emit(public, private)

    # -- rules ---------------------------------------------------------
    def occupied(self, except_suspect):
        return {(p["row"], p["column"]) for sus, p in self.pos.items()
                if sus != except_suspect and p["kind"] == "corridor"}

    def validate_move(self, seat, r):
        sus = self.characters[seat]
        origin = self.pos[sus]
        roll = sum(self.roll)
        path = [(c["row"], c["column"]) for c in r.get("path") or []]
        enter = r.get("enterRoom")
        if r.get("useSecretPassage"):
            if path or origin["kind"] != "room" or self.passages.get(origin["room"]) != enter:
                raise Fault("bad secret passage")
            return {"kind": "room", "room": enter}, path, "secret-passage"
        occ = self.occupied(sus)
        seen = set()
        prev = (origin["row"], origin["column"]) if origin["kind"] == "corridor" else None
        if prev:
            seen.add(prev)
        for i, cell in enumerate(path):
            if cell not in self.walk or cell in self.starts or cell in occ or cell in seen:
                raise Fault("illegal square %s" % (cell,))
            if prev is None:
                if origin["room"] not in self.doors.get(cell, set()):
                    raise Fault("must leave through own door")
            elif abs(prev[0] - cell[0]) + abs(prev[1] - cell[1]) != 1:
                raise Fault("non-adjacent step")
            seen.add(cell)
            prev = cell
        if len(path) > roll:
            raise Fault("path too long")
        if enter:
            end = path[-1] if path else (prev if origin["kind"] == "corridor" else None)
            if end is None or enter not in self.doors.get(end, set()):
                raise Fault("enterRoom not at its door")
            if origin["kind"] == "room" and origin["room"] == enter and self.strict:
                raise Fault("re-entered same room")
            return {"kind": "room", "room": enter}, path, "roll"
        if len(path) != roll:
            raise Fault("corridor move must use full roll (%d != %d) %r origin=%r" % (len(path), roll, r, origin))
        return {"kind": "corridor", "row": path[-1][0], "column": path[-1][1]}, path, "roll"

    def do_suggestion(self, seat, r, room):
        sus, wep = r.get("suspect"), r.get("weapon")
        if r.get("room") != room or sus not in SUSPECTS or wep not in WEAPONS:
            raise Fault("bad suggestion %r" % r)
        moved_from = copy.deepcopy(self.pos[sus])
        self.pos[sus] = {"kind": "room", "room": room}
        wfrom = self.weapon_pos[wep]
        self.weapon_pos[wep] = room
        pulled = self.char_seat.get(sus)
        if pulled == seat or (moved_from.get("kind") == "room" and moved_from.get("room") == room):
            pulled = None
        if pulled:
            self.pulled.add(pulled)
        cards = ["suspect:" + sus, "weapon:" + wep, "room:" + room]
        i = self.seats.index(seat)
        passed = []
        refuter = None
        for k in range(1, len(self.seats)):
            s = self.seats[(i + k) % len(self.seats)]
            if any(c in self.hands[s] for c in cards):
                refuter = s
                break
            passed.append(s)
        self.emit({"type": "suggestion-made", "turn": self.turn, "suggester": seat,
                   "suggestion": {"suspect": sus, "weapon": wep, "room": room},
                   "movedSuspectFrom": moved_from, "movedWeaponFrom": wfrom,
                   "pulledSeat": pulled, "passedSeats": passed, "refuter": refuter})
        if refuter:
            matching = [c for c in cards if c in self.hands[refuter]]
            card = None
            if refuter not in self.faulted:
                try:
                    resp = self.ask(refuter, "show-card")
                    if resp.get("type") == "card-shown" and resp.get("card") in matching:
                        card = resp["card"]
                    else:
                        raise Fault("bad card %r" % resp)
                except Fault as f:
                    self.fault(refuter, "show-card", str(f), ends_turn=False)
            if card is None:
                card = matching[0]
            pub = {"type": "card-shown", "turn": self.turn, "refuter": refuter, "shownTo": seat}
            priv = dict(pub, card=card)
            self.emit(pub, {seat: priv, refuter: priv})

    def fault(self, seat, during, reason, ends_turn=True):
        if self.verbose or True:
            print("  FAULT", seat, self.names[seat], during, reason)
        self.faulted.add(seat)
        self.eliminated.add(seat)
        ev = {"type": "detective-faulted", "turn": self.turn, "seat": seat, "during": during,
              "reason": reason, "eliminated": True, "unblockedInto": None, "endsTurn": ends_turn,
              "nextSeat": None, "nextRoll": None, "terminalReason": None, "solution": None}
        if ends_turn:
            self.finish_turn(ev)
        else:
            self.emit(ev)
            if len(self.alive()) <= 1:
                self.end(self.alive()[0] if self.alive() else None, "last-standing")

    def alive(self):
        return [s for s in self.seats if s not in self.eliminated]

    def next_seat(self):
        i = self.seats.index(self.active)
        for k in range(1, len(self.seats) + 1):
            s = self.seats[(i + k) % len(self.seats)]
            if s not in self.eliminated:
                return s
        return None

    def end(self, winner, reason):
        self.winner = winner
        self.reason = reason

    def finish_turn(self, ev):
        alive = self.alive()
        if self.reason:
            pass
        elif len(alive) == 1 and len(self.seats) > 1:
            self.end(alive[0], "last-standing")
        elif not alive:
            self.end(None, "all-eliminated")
        elif self.turn >= self.turn_limit:
            self.end(None, "turn-limit")
        if self.reason:
            ev.update(nextSeat=None, nextRoll=None, terminalReason=self.reason, solution=dict(self.solution))
            self.emit(ev)
            return
        nxt = self.next_seat()
        self.roll = self.dice()
        ev.update(nextSeat=nxt, nextRoll=list(self.roll))
        self.emit(ev)
        self.active = nxt
        self.turn += 1

    def accusation(self, seat, r):
        acc = {"suspect": r.get("suspect"), "weapon": r.get("weapon"), "room": r.get("room")}
        if acc["suspect"] not in SUSPECTS or acc["weapon"] not in WEAPONS or acc["room"] not in ROOMS:
            raise Fault("bad accusation")
        correct = acc == self.solution
        ev = {"type": "accusation-made", "turn": self.turn, "accuser": seat, "accusation": acc,
              "correct": correct, "eliminated": not correct, "unblockedInto": None,
              "nextSeat": None, "nextRoll": None, "terminalReason": None, "solution": None}
        if correct:
            self.end(seat, "solved")
        else:
            self.eliminated.add(seat)
            p = self.pos[self.characters[seat]]
            if p["kind"] == "corridor" and (p["row"], p["column"]) in self.doors:
                room = sorted(self.doors[(p["row"], p["column"])])[0]
                self.pos[self.characters[seat]] = {"kind": "room", "room": room}
                ev["unblockedInto"] = room
        self.finish_turn(ev)

    def play_turn(self):
        seat = self.active
        sus = self.characters[seat]
        try:
            r = self.ask(seat, "take-turn")
            t = r.get("type")
            if t == "accusation-made":
                return self.accusation(seat, r)
            if t == "suggestion-made":
                if seat not in self.pulled or self.pos[sus]["kind"] != "room":
                    raise Fault("suggestion without being pulled")
                self.pulled.discard(seat)
                self.do_suggestion(seat, r, self.pos[sus]["room"])
            elif t == "pawn-moved":
                to, path, via = self.validate_move(seat, r)
                frm = self.pos[sus]
                self.pos[sus] = to
                self.pulled.discard(seat)
                self.emit({"type": "pawn-moved", "turn": self.turn, "seat": seat, "suspect": sus,
                           "from": frm, "to": to, "path": r.get("path") or [], "roll": list(self.roll),
                           "cost": len(path), "via": via})
                if to["kind"] == "room":
                    r2 = self.ask(seat, "make-suggestion")
                    if r2.get("type") != "suggestion-made":
                        raise Fault("expected suggestion")
                    self.do_suggestion(seat, r2, to["room"])
            elif t == "turn-ended":
                self.pulled.discard(seat)
                return self.finish_turn({"type": "turn-ended", "turn": self.turn, "seat": seat,
                                         "nextSeat": None, "nextRoll": None, "terminalReason": None, "solution": None})
            else:
                raise Fault("bad take-turn reply %r" % r)
            if self.reason:
                return
            r3 = self.ask(seat, "accuse-or-end")
            if r3.get("type") == "accusation-made":
                return self.accusation(seat, r3)
            if r3.get("type") != "turn-ended":
                raise Fault("bad accuse-or-end %r" % r3)
            self.finish_turn({"type": "turn-ended", "turn": self.turn, "seat": seat,
                              "nextSeat": None, "nextRoll": None, "terminalReason": None, "solution": None})
        except Fault as f:
            self.fault(seat, "take-turn", str(f))

    def run(self):
        self.start()
        while not self.reason:
            self.play_turn()
        for b in self.bots.values():
            b.close()
        return self.winner, self.reason


def main():
    from lowprio import set_low_priority
    set_low_priority()
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--bots", default="ours,example,example")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()
    specs = args.bots.split(",")
    wins = {}
    reasons = {}
    turns = []
    rng = random.Random(args.seed)
    t0 = time.time()
    for g in range(args.n):
        # Rotate seats so nobody always moves first.
        rot = g % len(specs)
        order = specs[rot:] + specs[:rot]
        game = ClueGame(order, seed=rng.randrange(1 << 30), verbose=args.v)
        winner, reason = game.run()
        name = game.names[winner] + "@" + winner if winner else None
        key = game.names[winner] if winner else None
        wins[key] = wins.get(key, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1
        turns.append(game.turn)
        mx = max(st["max"] for s, st in game.stats.items() if game.names[s] == "ours") if "ours" in specs else 0
        print("game %d: winner=%s reason=%s turns=%d maxdecision=%.1fs" % (g, name, reason, game.turn, mx), flush=True)
        for s, b in game.bots.items():
            if b.stderr and game.names[s] == "ours":
                print("   stderr:", b.stderr[-1][-800:])
    print("wins:", wins)
    print("reasons:", reasons, "avg turns: %.1f" % (sum(turns) / len(turns)), "elapsed %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
