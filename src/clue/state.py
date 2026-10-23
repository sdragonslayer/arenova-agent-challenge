"""Rebuild Clue game state and ownership knowledge from the visible history."""
from clue.board import Board, rc
from clue.deduce import Knowledge


class Suggestion:
    __slots__ = ("turn", "suggester", "suspect", "weapon", "room", "passed", "refuter", "card")

    def __init__(self, ev):
        s = ev.get("suggestion") or {}
        self.turn = ev.get("turn")
        self.suggester = ev.get("suggester")
        self.suspect = s.get("suspect")
        self.weapon = s.get("weapon")
        self.room = s.get("room")
        self.passed = list(ev.get("passedSeats") or [])
        self.refuter = ev.get("refuter")
        self.card = None

    def cards(self):
        return ["suspect:" + self.suspect, "weapon:" + self.weapon, "room:" + self.room]


class ClueState:
    def __init__(self, history):
        items = sorted(history, key=lambda it: it.get("seq", 0))
        events = [it.get("event") or {} for it in items]
        start = None
        for ev in events:
            if ev.get("type") == "game-started":
                start = ev
                break
        if start is None:
            raise ValueError("no game-started in history")
        self.start = start
        self.me = start["you"]["seat"]
        self.my_char = start["you"]["character"]
        self.hand = list(start.get("hand") or [])
        self.seats = list(start.get("turnOrder") or start.get("seats"))
        self.characters = dict(start.get("characters") or {})
        self.char_seat = {v: k for k, v in self.characters.items()}
        self.hand_sizes = dict(start.get("handSizes") or {})
        self.suspects = list(start["suspects"])
        self.weapons = list(start["weapons"])
        self.rooms = list(start["rooms"])
        self.turn_limit = int(start.get("turnLimit") or 60)
        self.board = Board(start["board"])
        self.positions = {}
        for sus, pos in (start.get("suspectPositions") or {}).items():
            self.positions[sus] = self._pos(pos)
        self.roll = list(start.get("roll") or [0, 0])
        self.active_seat = start.get("activeSeat")
        self.turn = int(start.get("turn") or 1)
        self.pulled = False
        self.eliminated = set()
        self.faulted = set()
        self.suggestions = []
        self.shown_by_me = {}       # suggester seat -> set(cards) I've shown them
        self.failed_accusations = []
        self.terminal = None
        self.my_suggestions_this_turn = 0

        for ev in events:
            t = ev.get("type")
            if t == "pawn-moved":
                self.positions[ev["suspect"]] = self._pos(ev["to"])
                if ev.get("seat") == self.me:
                    self.pulled = False
            elif t == "suggestion-made":
                sg = Suggestion(ev)
                self.suggestions.append(sg)
                self.positions[sg.suspect] = ("room", sg.room)
                if sg.suggester == self.me:
                    self.pulled = False
                elif ev.get("pulledSeat") == self.me:
                    self.pulled = True
            elif t == "card-shown":
                card = ev.get("card")
                for sg in reversed(self.suggestions):
                    if sg.turn == ev.get("turn") and sg.refuter == ev.get("refuter"):
                        if card:
                            sg.card = card
                        break
                if card and ev.get("refuter") == self.me:
                    self.shown_by_me.setdefault(ev.get("shownTo"), set()).add(card)
            elif t == "accusation-made":
                acc = ev.get("accusation") or {}
                if not ev.get("correct"):
                    self.failed_accusations.append(
                        ["suspect:" + acc.get("suspect"), "weapon:" + acc.get("weapon"), "room:" + acc.get("room")])
                if ev.get("eliminated"):
                    self.eliminated.add(ev.get("accuser"))
                if ev.get("unblockedInto"):
                    ch = self.characters.get(ev.get("accuser"))
                    if ch:
                        self.positions[ch] = ("room", ev["unblockedInto"])
                if ev.get("accuser") == self.me:
                    self.pulled = False
                self._advance(ev)
            elif t == "turn-ended":
                if ev.get("seat") == self.me:
                    self.pulled = False
                self._advance(ev)
            elif t == "detective-faulted":
                self.faulted.add(ev.get("seat"))
                if ev.get("eliminated", True):
                    self.eliminated.add(ev.get("seat"))
                if ev.get("unblockedInto"):
                    ch = self.characters.get(ev.get("seat"))
                    if ch:
                        self.positions[ch] = ("room", ev["unblockedInto"])
                if ev.get("endsTurn"):
                    self._advance(ev)

    def _advance(self, ev):
        if ev.get("terminalReason"):
            self.terminal = ev.get("terminalReason")
        if ev.get("nextRoll"):
            self.roll = list(ev["nextRoll"])
        if ev.get("nextSeat"):
            self.active_seat = ev["nextSeat"]
        if ev.get("turn") is not None:
            self.turn = int(ev["turn"]) + 1

    @staticmethod
    def _pos(pos):
        if pos.get("kind") == "room":
            return ("room", pos["room"])
        return ("corridor", rc(pos))

    # -- convenience -------------------------------------------------------
    @property
    def my_pos(self):
        return self.positions.get(self.my_char)

    def blocked(self):
        """Corridor squares occupied by other pawns."""
        out = set()
        for sus, pos in self.positions.items():
            if sus != self.my_char and pos[0] == "corridor":
                out.add(pos[1])
        return out

    def category(self, card):
        return card.split(":", 1)[0]

    def all_cards(self):
        return (["suspect:" + s for s in self.suspects]
                + ["weapon:" + w for w in self.weapons]
                + ["room:" + r for r in self.rooms])

    def my_remaining_turns(self):
        """How many more turns we get, counting the current one if it is ours."""
        active = [s for s in self.seats if s not in self.eliminated]
        if self.me not in active:
            return 0
        if self.active_seat in active:
            i = active.index(self.active_seat)
        else:
            i = 0
        count = 0
        for t in range(self.turn, self.turn_limit + 1):
            if active[i % len(active)] == self.me:
                count += 1
            i += 1
        return count

    def knowledge(self):
        cards = self.all_cards()
        cats = {c: self.category(c) for c in cards}
        sizes = [int(self.hand_sizes.get(s, 0)) for s in self.seats]
        kn = Knowledge(cards, cats, len(self.seats), sizes)
        idx = {s: i for i, s in enumerate(self.seats)}
        me = idx[self.me]
        hand = set(self.hand)
        for c in cards:
            if c in hand:
                kn.set_owner(c, me)
            else:
                kn.exclude(c, me)
        for sg in self.suggestions:
            try:
                triple = sg.cards()
                for p in sg.passed:
                    if p in idx:
                        for c in triple:
                            kn.exclude(c, idx[p])
                if sg.refuter is None:
                    for s in self.seats:
                        if s != sg.suggester:
                            for c in triple:
                                kn.exclude(c, idx[s])
                elif sg.refuter in idx:
                    if sg.card:
                        kn.set_owner(sg.card, idx[sg.refuter])
                    elif sg.refuter != self.me:
                        kn.add_clause(idx[sg.refuter], triple)
            except KeyError:
                continue
        for acc in self.failed_accusations:
            try:
                kn.add_not_solution(acc)
            except KeyError:
                pass
        return kn
