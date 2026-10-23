"""Unit tests (stdlib unittest). Run: python -m unittest discover -s tests -v"""
import json
import os
import random
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("CRUCIBLE_QUIET", "1")

from common.timer import Budget  # noqa: E402


# ----------------------------------------------------------------------
class ConnectFourTests(unittest.TestCase):
    def brute(self, cur, mask, moves):
        """Plain negamax returning sign of the result for the side to move."""
        from connect4.solver import BOARD
        from connect4.board import legal_columns, wins_now, play
        if mask == BOARD:
            return 0
        best = -1
        for c in legal_columns(mask):
            if wins_now(cur, mask, c):
                return 1
            ncur, nmask = play(cur, mask, c)
            v = -self.brute(ncur, nmask, moves + 1)
            best = max(best, v)
            if best == 1:
                break
        return best

    def test_solver_matches_bruteforce_late_positions(self):
        from connect4.board import play_columns, legal_columns, wins_now
        from connect4.solver import Solver
        rng = random.Random(3)
        checked = 0
        while checked < 25:
            cols = []
            cur = mask = 0
            ok = True
            for _ in range(31):
                legal = legal_columns(mask)
                c = rng.choice(legal)
                if wins_now(cur, mask, c):
                    ok = False
                    break
                cols.append(c)
                cur, mask, _ = play_columns(cols)
            if not ok:
                continue
            s = Solver()
            weak = s.solve(cur, mask, len(cols), weak=True)
            self.assertEqual((weak > 0) - (weak < 0), self.brute(cur, mask, len(cols)), cols)
            checked += 1

    def test_agent_blocks_and_wins(self):
        from connect4.agent import Agent
        a = Agent("seat-1")

        def msg(cols):
            h = [{"seq": 0, "atMs": 0, "event": {"type": "game-started", "red": "seat-1", "yellow": "seat-2", "you": 1}}]
            for i, c in enumerate(cols):
                h.append({"seq": i + 1, "atMs": 0, "event": {"type": "disc-dropped", "column": c}})
            return {"type": "decision", "decision": {"kind": "disc-dropped"}, "history": h, "deadlineMs": 15000}

        # Red has three in column 0 -> must win there.
        self.assertEqual(a.decide(msg([0, 1, 0, 1, 0, 2]), Budget(3))["column"], 0)
        # Yellow has 4,5,6 on the bottom row -> red must block at column 3.
        r = a.decide(msg([0, 4, 0, 5, 1, 6]), Budget(3))["column"]
        self.assertEqual(r, 3)

    def test_book_moves_are_legal(self):
        from connect4.agent import load_book
        book = load_book()
        for k, c in list(book.items())[:2000]:
            self.assertTrue(0 <= c <= 6)


# ----------------------------------------------------------------------
class ClueDeductionTests(unittest.TestCase):
    def test_soundness_on_simulated_games(self):
        """The true deal must stay feasible and certainty must name the true solution."""
        import sim_clue
        from clue.state import ClueState
        from clue.agent import Analysis
        rng = random.Random(7)
        for trial in range(6):
            game = sim_clue.ClueGame(["ours"] * rng.choice([3, 4, 5, 6]), seed=rng.randrange(1 << 30))
            game.start()
            for _ in range(rng.randrange(4, 18)):
                if game.reason:
                    break
                game.play_turn()
            for seat in game.seats:
                st = ClueState(game.history_for(seat))
                an = Analysis(st, Budget(5), n_samples=200)
                kn = an.kn
                truth = {}
                for i, s in enumerate(st.seats):
                    for c in game.hands[s]:
                        truth[kn.index[c]] = i
                for k, cat in enumerate(("suspect", "weapon", "room")):
                    truth[kn.index["%s:%s" % (cat, game.solution[cat])]] = kn.n + k
                for i, o in truth.items():
                    self.assertTrue((an.mat[i] >> o) & 1, "true owner pruned: %s" % kn.cards[i])
                if an.certain:
                    t, _ = an.best_triple()
                    names = [kn.cards[i].split(":")[1] for i in t]
                    self.assertEqual(names, [game.solution["suspect"], game.solution["weapon"], game.solution["room"]])
            for b in game.bots.values():
                b.close()


class ClueMovementTests(unittest.TestCase):
    def test_generated_moves_pass_engine_validation(self):
        import sim_clue
        from clue.state import ClueState
        from clue.agent import Agent
        rng = random.Random(11)
        agent = Agent("seat-1")
        for trial in range(40):
            game = sim_clue.ClueGame(["ours"] * 4, seed=rng.randrange(1 << 30))
            game.start()
            # Scatter pawns at random corridor squares and rooms.
            cells = sorted(game.walk - game.starts)
            for sus in game.pos:
                if rng.random() < 0.5:
                    r, c = rng.choice(cells)
                    game.pos[sus] = {"kind": "corridor", "row": r, "column": c}
                else:
                    game.pos[sus] = {"kind": "room", "room": rng.choice(sim_clue.ROOMS)}
            game.log[0][1]["seat-1"]["suspectPositions"] = game.pos
            for _ in range(5):
                game.roll = [rng.randint(1, 6), rng.randint(1, 6)]
                game.log[0][1]["seat-1"]["roll"] = game.roll
                msg = {"type": "decision", "decision": {"kind": "take-turn"},
                       "history": game.history_for("seat-1"), "deadlineMs": 15000}
                for fn in (lambda m: agent.decide(m, Budget(5)), agent.fallback):
                    r = fn(msg)
                    if r["type"] == "pawn-moved":
                        game.validate_move("seat-1", r)  # raises Fault if illegal
                    else:
                        self.assertIn(r["type"], ("turn-ended", "accusation-made", "suggestion-made"))
            for b in game.bots.values():
                b.close()


# ----------------------------------------------------------------------
class SecretDictatorTests(unittest.TestCase):
    def test_claim_parsing(self):
        from sd.claims import parse_drawn, parse_received, parse_investigation
        self.assertEqual(parse_drawn("As President I drew FFL (2 Fascist + 1 Liberal)"), 1)
        self.assertEqual(parse_drawn("drew three fascist policies"), 0)
        self.assertEqual(parse_drawn("I drew two liberals and a fascist"), 2)
        self.assertEqual(parse_received("I received FF (2 Fascist) and enacted Fascist."), 0)
        self.assertEqual(parse_received("got one liberal and one fascist"), 1)
        self.assertEqual(parse_investigation("I investigated seat-4: they are Fascist.", "seat-4"), "fascist")
        self.assertIsNone(parse_investigation("seat-2 is great", "seat-4"))

    def test_hypothesis_counts(self):
        from sd.belief import hypotheses
        seats = ["seat-%d" % i for i in range(1, 8)]
        self.assertEqual(len(hypotheses(seats, "seat-1", "liberal")), 60)
        self.assertEqual(len(hypotheses(seats, "seat-1", "dictator")), 15)
        self.assertEqual(len(hypotheses(seats, "seat-1", "fascist", ["seat-2"], "seat-3")), 1)
        self.assertEqual(len(hypotheses(seats)), 105)

    def test_full_games_no_faults(self):
        import sim_sd
        for seed in range(3):
            g = sim_sd.SDGame(["ours"] * 4 + ["simple"] * 3, seed=seed)
            g.run()
            self.assertTrue(g.reason and not g.reason.startswith("aborted"), g.reason)
            self.assertEqual(sum(v for s, v in g.faults.items() if g.specs[s] == "ours"), 0)


# ----------------------------------------------------------------------
class ProtocolTests(unittest.TestCase):
    def test_entrypoint_replies_once_per_request(self):
        p = subprocess.Popen([sys.executable, os.path.join(ROOT, "src", "main.py")], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        reqs = [
            {"type": "init", "game": "connect-four", "seat": "seat-1"},
            {"type": "decision", "decision": {"requestId": "turn:1", "seat": "seat-1", "kind": "disc-dropped"},
             "history": [{"seq": 0, "atMs": 0, "event": {"type": "game-started", "red": "seat-1", "yellow": "seat-2", "you": 1}}],
             "deadlineMs": 15000},
            # Malformed history: must still get a (fallback) reply.
            {"type": "decision", "decision": {"requestId": "turn:3", "seat": "seat-1", "kind": "disc-dropped"},
             "history": [{"seq": 0, "atMs": 0, "event": {"type": "disc-dropped", "column": 3}}], "deadlineMs": 15000},
        ]
        out, _ = p.communicate("\n".join(json.dumps(r) for r in reqs) + "\n", timeout=60)
        lines = [json.loads(l) for l in out.splitlines() if l.strip()]
        self.assertEqual(len(lines), len(reqs))
        self.assertEqual(lines[0], {})
        self.assertEqual(lines[1]["type"], "disc-dropped")


if __name__ == "__main__":
    unittest.main()
