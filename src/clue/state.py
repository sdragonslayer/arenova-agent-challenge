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
        self.characters = dict(start.get("characters") or {})
        self.seats = list(start.get("turnOrder") or start.get("seats") or self.characters)
        if self.me not in self.seats:
            self.seats.append(self.me)
        self.char_seat = {v: k for k, v in self.characters.items()}
        self.suspects = list(start["suspects"])
        self.weapons = list(start["weapons"])
        self.rooms = list(start["rooms"])
        self.hand_sizes = dict(start.get("handSizes") or {})
        if any(s not in self.hand_sizes for s in self.seats):
            # Not published: assume a round-robin deal starting with the first seat.
            dealt = len(self.suspects) + len(self.weapons) + len(self.rooms) - 3
            base, extra = divmod(dealt, len(self.seats))
            for i, s in enumerate(self.seats):
                self.hand_sizes.setdefault(s, base + (1 if i < extra else 0))
            self.hand_sizes[self.me] = len(start.get("hand") or [])
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
                if "passedSeats" not in ev and sg.suggester in self.seats:
                    sg.passed = self._seats_between(sg.suggester, sg.refuter)
                self.suggestions.append(sg)
                self.positions[sg.suspect] = ("room", sg.room)
                if sg.suggester == self.me:
                    self.pulled = False
                elif ev.get("pulledSeat") == self.me:
                    self.pulled = True
            elif t == "card-shown":
                card = ev.get("card")
                for sg in reversed(self.suggestions):
                    same_turn = ev.get("turn") is None or sg.turn is None or sg.turn == ev.get("turn")
                    same_refuter = ev.get("refuter") is None or sg.refuter == ev.get("refuter")
                    if same_turn and same_refuter:
                        if card:
                            sg.card = card
                        if card and sg.refuter == self.me:
                            self.shown_by_me.setdefault(ev.get("shownTo") or sg.suggester, set()).add(card)
                        break
            elif t == "accusation-made":
                acc = ev.get("accusation") or ev
                names = [acc.get("suspect"), acc.get("weapon"), acc.get("room")]
                failed = ev.get("correct") is False or (ev.get("correct") is None and ev.get("eliminated"))
                if failed and all(isinstance(x, str) for x in names):
                    self.failed_accusations.append(
                        ["suspect:" + names[0], "weapon:" + names[1], "room:" + names[2]])
                if ev.get("eliminated") or ev.get("correct") is False:
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

    def _seats_between(self, suggester, refuter):
        """Seats asked before `refuter` (all others if nobody refuted)."""
        i = self.seats.index(suggester)
        out = []
        for k in range(1, len(self.seats)):
            s = self.seats[(i + k) % len(self.seats)]
            if s == refuter:
                break
            out.append(s)
        return out

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
