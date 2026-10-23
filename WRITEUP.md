# Crucible agent write-up

One Python 3.13 program (standard library only) plays all three games. Each game is a separate module behind a shared protocol shell. The guiding principle: **deterministic, verifiable reasoning first; the language model only where language itself is the task.**

## 1. Architecture and reliability

A fault costs a lot: a forfeit in Connect Four, elimination in Clue, an aborted match in Secret Dictator's first round. So reliability is built in:

- **Watchdog.** `src/main.py` reads one request per line and runs each decision in a worker thread, with a soft time budget (a fraction of `deadlineMs`, capped per game).
- **Fallbacks.** Every game agent has a cheap `fallback()` that derives a *legal* move from the history. It is sent if the main logic raises or runs over time. In Clue, if even the state reader fails, a last-resort reply is built straight from the raw events.
- **Tolerant parsing.** The Clue state reader works out optional fields it doesn't find (turn order, hand sizes, which seats passed) instead of failing on them.
- **Cooperative stopping.** Long computations check their budget and stop on their own, so a timed-out worker doesn't keep stealing CPU from the next decision.
- **State rebuilt each time.** State is rebuilt from the full history on every request, which is the source of truth. The only state kept between requests is what *we* decided to say (our claims), so our story stays consistent.
- **Log budget.** Logging stays under the runner's 200-line cap, and low-priority lines stop early so late errors are still captured.

## 2. Connect Four: solved play

- **Exact solver.** A bitboard negamax in the style of Pascal Pons: null-window search, a transposition table, centre-first and threat-count move ordering, and pruning of moves that lose immediately.
- **Opening book, computed offline.** Python searches about 80k nodes/s, too slow to solve the opening live. `tools/c4_book.py` proves the first-player win up to ply 10: it guesses a red move at each red node, then checks all yellow replies. Leaves are solved in parallel and failed guesses are retried. The book stores a proven winning move for every position on our red line (with left-right mirroring).
- **Live play.**
  - Use the book when the position is in it.
  - Otherwise, weak-solve each child in heuristic order and play the first proven win.
  - If time runs out, use iterative-deepening alpha-beta with a threat-parity evaluation (odd-row threats for the first player, even-row for the second).
- **Losing positions.** When every move loses, we pick the move the heuristic rates as most resistant, so an imperfect opponent still has chances to go wrong.

## 3. Clue: exact deduction plus information gain

### Deduction
The hidden state is the owner of each of the 21 cards: a player, or one of three solution slots. Everything we learn becomes a constraint:

- our own hand, and cards shown to us;
- `passedSeats` cannot hold any of the suggested cards;
- an unseen refutation means "this refuter holds at least one of these three cards";
- an unrefuted suggestion clears everyone except the suggester;
- hand sizes are exact;
- the solution holds exactly one card per category;
- a failed accusation means "not all three of these cards".

An exact backtracking CSP tightens each card's possible owners. We accuse only when every category has exactly one feasible solution card, so a certain accusation is never wrong.

The search branches on the tightest unsatisfied "refuter holds one of these" clause first, using disjoint branches: card 1 goes to the refuter; otherwise card 2 does; and so on. At every node it also runs an exact capacity check, a bipartite matching of the remaining cards to the hand slots left. Each full assignment found is reused as a witness for all of its (card, owner) pairs, which skips most of the remaining searches. On clause-heavy six-player positions this cut tightening from up to 17 s to about 0.02 s. The search also stops at a deadline. If it runs out of time, an unresolved owner is treated as still possible, so the time limit can never lead to a wrong "certain" accusation.

### Probabilities
A swap-move Markov chain samples consistent deals, which are uniform under random dealing. These samples give probabilities for each solution triple and drive every other decision.

### Suggestions
For each suspect and weapon in the current room, we simulate the refutation (refutation order, which player holds which card) on every sample. We then score the expected information gained about each solution category, plus a small bonus for information about players' hands.

### Movement
- **Room values.** Each room is worth its best suggestion. A corridor square is worth the best room value discounted by the exact 2d6 probability of reaching that room on later turns, and by the turns we have left before the limit.
- **Legal paths.** Moves are built conservatively (exact roll, simple paths, no start squares or occupied squares, doors only as the destination) and checked by an independent validator before they are sent.

### Accusing under the turn limit
Besides certain accusations:
- We gamble on our last turn, because a no-winner game is worth nothing to us.
- We gamble earlier when `opponent_threat()` estimates that an opponent can already name the solution. This uses the same samples: for each sampled deal, rebuild what each opponent has seen and apply elimination.

### Showing cards
Prefer a card this suggester has already seen from us, then one we've shown others. This leaks as little new information as possible.

## 4. Secret Dictator: Bayesian roles, scripted language

### Beliefs
We keep a posterior over every hidden assignment consistent with what we know: 60 hypotheses as a liberal, 15 as the dictator, and 105 for a "neutral observer" model, used to estimate how suspicious the table finds each player, ourselves included. The evidence is folded in through soft likelihood models of liberal and fascist behaviour:

- **Legislation.** For each government we enumerate the draw (a binomial over the estimated liberal fraction of the pile, tracked through reshuffles), the president's discard, the chancellor's choice, and both players' claims. Fascists "cover" fascist policies with fascist-heavy claims; liberals tell the truth, with noise.
- **Our own private knowledge.** The cards we drew or received pin down parts of that chain exactly. For example, if we pass a mixed hand and a fascist policy comes back, the chancellor is strongly suspect.
- **Behaviour.** Votes (team members back governments that include teammates), nominations, investigation claims and executions.
- **Hard facts.** `chancellor-confirmed-not-dictator`, executions that didn't end the game, and our own investigation results.

Every soft model is mixed with role-independent noise, so one misparsed statement can never be decisive.

### Policies
- **Liberal:**
  - Always pass or enact Liberal; tell the truth in a fixed, easily parsed format.
  - Vote yes when a government's estimated risk is no worse than the average alternative. Be laxer when a failed vote would trigger chaos.
  - After three fascist policies, never risk a likely Dictator as chancellor.
  - Investigate the most uncertain player; execute the most likely Dictator.
- **Fascist:**
  - Knows the team. Nominates teammates when the table trusts them.
  - Passes FF when possible, then claims FFF. Covers for fascist teammates and frames liberals.
  - Pushes the Dictator to chancellor after three fascist policies.
  - Votes are deliberately "noisy" so the team doesn't vote as a bloc. Self-play tuning showed that bloc voting was the biggest giveaway.
- **Dictator:** plays like a model liberal to build trust, then aims to be elected chancellor.

### Language and safety
The LLM does two narrow jobs:
1. Extracts claims ("drew FFL", "received FF", investigation results) from other players' statements into a fixed JSON schema, in one batched call per discussion, with a regex parser as fallback.
2. Optionally rewords our deterministic talking points.

It never decides an action, never sees our hidden role, and its text is thrown away if it drops our factual claim or breaks the 320-character limit. Other bots' statements are treated as untrusted data. That makes us robust to prompt injection, and the call budget stays far below the per-match cap. Round 1, where a fault aborts the match, is fully deterministic.

## 5. Testing

- **Local engines** (`tools/sim_c4.py`, `sim_clue.py`, `sim_sd.py`) reproduce the documented protocol, including per-seat visibility and fault handling. They can drive the real `src/main.py` over stdin/stdout.
- **Opponents:**
  - Connect Four: random and greedy bots.
  - Clue: Arenova's example bot, plus our agent with and without threat-aware accusations.
  - Secret Dictator: a simple scripted bot (honest liberals, greedy fascists), plus self-play.
- **Mock AI relay** (`tools/mock_relay.py`), with injected latency and failures, to test the LLM paths for free.
- **Unit tests** (`tests/test_all.py`):
  - solver vs brute force;
  - Clue deduction soundness (the true deal is never pruned, and certainty always names the true solution);
  - Clue solver witnesses satisfy every constraint, and clause-heavy positions solve quickly;
  - every generated Clue move passes the engine's validator;
  - claim parsing;
  - full Secret Dictator games with zero faults;
  - entrypoint protocol fuzzing.

Results (local; see `CLAUDE.md` for the latest):
- **Connect Four:** wins every game against random and greedy, as either colour.
- **Clue:** zero faults; every self-play game ends in a correct accusation (about 14 total turns with 4 players). Decisions take about 1 s, and at most 0.9 s across 16 six-player self-play games. The example bot did not win a single game against our agent.
- **Secret Dictator:** zero faults, including with 20% relay failures.
  - Self-play: liberals win about 84%.
  - Against the scripted baseline: our fascists win 79% vs the baseline's 72%; our liberals win 32% vs 26–30%.
  - Fascist parameters were tuned in self-play (`tools/tune_sd.py`). Making the team's votes less bloc-like doubled the fascist-team win rate against our own Bayesian liberals, from 15.7% to about 30%. Purely aggressive or purely "deep cover" legislation both did worse.
- **Clue threat-aware accusations:** in 4-player games against three copies of our agent without that feature, the threat-aware version won 14 of 40 (its fair share is 10).

## 6. Limitations and future work

- **Clue movement assumptions.** We don't know some board rules (passing through doors, re-entering a room), so movement is conservative and may give up a little speed.
- **Fit of the Secret Dictator model.** The behaviour models are hand-set. They are tuned against our own bots, which may not resemble the tournament field. Learning the likelihood parameters from replays would be the next step.
- **Second-player Connect Four.** Against perfect play there is nothing to gain. A yellow-side book that maximises how long we survive and steers toward tricky positions would help against imperfect opponents.
