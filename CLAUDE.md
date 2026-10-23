# Arenova Crucible agent challenge

One Python 3.13 ZIP plays three games: Connect Four (tutorial), Clue (medium weight) and Secret Dictator (most weight).
**Submissions lock on 2026-11-01 at 23:59 CST.** The full plan is in `~/.claude/plans/pasted-content-id-4198-choose-a-glowing-hoare.md`.

## ⚠ TODO for you (needs the Arenova website) — do these first
- [ ] **Confirm the Python runtime id** for `crucible.json` (currently a guess, `"python313"`). A wrong value fails upload validation.
- [ ] **Get the real Clue board.** This is the biggest open risk: all Clue movement testing so far used a board I made up (`tools/clue_board.py`).
  1. Run one practice Clue match.
  2. Copy the full `game-started` event from the replay or history and save it as `tools/fixtures/clue_game_started.json`. `clue_board.load()` and the simulator pick it up automatically.
  3. From the replay, note: can a pawn re-enter the room it just left, and can it walk through or stop on a door square? Does the turn counter skip eliminated players?
  4. Ask Claude to re-run `python -m unittest tests.test_all.ClueMovementTests tests.test_all.ClueDeductionTests` and `tools/sim_clue.py` on the real board, and to relax `src/clue/board.py` if the engine allows more.
- [ ] **Upload `dist/submission.zip`** and play one practice match each of Connect Four and Secret Dictator. Paste any errors or odd replay behaviour into a Claude session. Each match counts against the $2/day practice cap.
- [ ] **Rebuild the zip after any change:** run `python tools/build_zip.py`, which writes `dist/submission.zip` and checks the limits. The last build, on 2026-10-23, was verified from a clean extracted copy.
- [ ] **Optional, when the machine is idle:** resume the Connect Four book with `python tools/c4_book.py 10 2` (low CPU, resumable, Ctrl+C anytime). When it prints `wrote N entries`, rebuild the zip.
- [ ] **Before the 2026-11-01 23:59 CST lock:** submit the final zip and choose it as your tournament agent.

## Platform facts (from the docs)
- **`crucible.json`** sits at the zip root with `runtime` and `entrypoint`. The runtime id `python313` is a **guess**; confirm it on the site.
- **Protocol:** one long-running process per seat, one JSON object per line on stdin, one reply per line on stdout. Logs go to stderr only (capped at 200 lines × 4000 chars).
- **Init:** `{"type":"init","game":...,"seat":...}`; reply `{}`.
- **Decisions:** `{"type":"decision","decision":{requestId,seat,kind},"history":[{seq,atMs,event}],"deadlineMs":...}`. Reply with the bare game-specific object.
- **History:** each request carries the full visible history. No end-of-game message is sent.
- **Deadline:** 65 s per decision. A bad or late reply means forfeit (Connect Four), elimination (Clue), or in Secret Dictator an aborted match if it happens in round 1 and a baseline move after that.
- **Match limits:** 5 min (Connect Four), 13 min (Clue), 20 min (Secret Dictator).
- **Resources per seat:** 1 core, 512 MiB RAM, no network except the AI relay.
- **AI relay:** `$OPENAI_BASE_URL`, `$OPENAI_API_KEY`, `$CRUCIBLE_AI_MODEL` (openai/gpt-6-luna). Caps: $0.05 per call, $0.25 or 1024 calls per seat per match, $2 per day across all practice.
- **ZIP limits:** 2 MB compressed, 12 MB expanded, 250 files, 4 MB per file.

## Layout
```
crucible.json          runtime + entrypoint (src/main.py)
src/main.py            stdin loop; each decision runs in a worker thread under a watchdog -> legal fallback on error/timeout
src/common/            io (reply + log budget, CRUCIBLE_QUIET), timer (Budget), llm (stdlib relay client)
src/connect4/          solver.py (Pons-style bitboard negamax), heuristic.py, agent.py (book -> exact solve -> heuristic), book.txt (generated)
src/clue/              state.py (history -> state + constraints), deduce.py (exact CSP + MCMC deal sampler),
                       board.py (BFS/paths/validation), agent.py (accuse / info-gain suggestions / movement / card showing)
src/sd/                state.py, belief.py (Bayes over 60 role hypotheses), claims.py (regex + LLM claim parsing),
                       policy.py (role policies, PARAMS tunable via SD_PARAMS), talk.py (templates + optional LLM phrasing), agent.py
tools/                 sim_c4.py, sim_clue.py, sim_sd.py (local engines), tune_sd.py, c4_book.py, mock_relay.py,
                       build_zip.py, proc.py (bot wrappers), lowprio.py, clue_board.py (synthetic board), bots/clue_example.py
tests/test_all.py      stdlib unittest
```

## Key design decisions
- Standard library only, so there's no build step.
- Every handler has a cheap `fallback(msg)` that returns a legal move. `main.py` uses it whenever `decide` raises or runs past its time limit (soft limit: per-game cap of 7/25/40 s).
- **Clue movement is deliberately conservative**, because the real rules are uncertain and an illegal move eliminates us:
  - door squares only as the last step (or as the first step when leaving through our own door);
  - never re-enter the room we just left;
  - corridor moves use the exact roll.
- **Clue accusations:** accuse when exact CSP certainty is reached. Gamble only on the final turn, or when `opponent_threat()` says an opponent is likely already certain.
- **Secret Dictator:** the LLM never makes decisions. It only extracts claims into a fixed JSON schema and rewords statements, and its output is rejected if the claim text is altered. This is our defence against prompt injection. Round 1 uses no LLM and is fully deterministic, because a fault there aborts the match.

## Status (as of 2026-10-23)
- Working end to end through `src/main.py` for all 3 games (local engines; protocol tests pass).
- **Connect Four:**
  - Beats random and greedy as both colours.
  - Opening-book generation is **paused and resumable**: stopped mid-round 12 on 2026-10-23, with solved leaves cached in `tools/c4_leaf_cache.json` (commit this file so progress isn't lost). `src/connect4/book.txt` is **not written yet**; it only appears when the proof completes.
  - Without the book, the agent searches live (exact solve from ~ply 10, heuristic before that).
- **Clue:** self-play has zero faults and a correct solve every game (avg ~14 total turns with 4 players), with ~1 s per decision. It beats the example bot, which also faults on its own (stale roll after `detective-faulted`).
- **Secret Dictator:**
  - Zero faults, including with the mock relay at a 20% failure rate.
  - In self-play our fascist team now wins ~30% (it was 15.7% before vote tuning). See TODO 4 for the details.
  - Against the simple baseline: our fascists win 79% (baseline fascists 72%) and our liberals win 32% (baseline 26–30%).
- **Tests:** 8 of 9 passed. The failing Connect Four test was a bad test position (a double threat); it has been fixed but not yet re-run.

## TODO (in priority order)
1. **Confirm the Python runtime id** for `crucible.json` on arenova-ctf.com. Then do one practice match per game. Save the replays or histories into `tools/fixtures/`, especially a real Clue `game-started` payload as `tools/fixtures/clue_game_started.json` (`clue_board.load()` picks it up automatically). Re-run the Clue sims and tests on the real board.
2. Check the real engine's rules against our assumptions: can a pawn re-enter its own room, walk through door squares, or stand on a door square? Does the turn counter skip eliminated players? If the real rules are looser, relax `clue/board.py` and check our moves still validate.
3. **Finish the Connect Four book** (`python tools/c4_book.py 10 2`, low CPU, resumable). Then confirm `book.txt` is well under 4 MB and that the agent loads it (it logs "c4 book entries"). Consider a yellow-side book after that.
4. ~~Secret Dictator tuning~~ **Done (2026-10-23).**
   - Round 2 in self-play (105 games each), fascist-team win rate:

     | Variant (`mate_yes` / `libgov_yes`) | Win rate |
     |---|---|
     | qv-55-90 (0.55 / 0.9) | 32.4% |
     | quiet-votes (0.7 / 0.85) | 29.5% |
     | early-agg, tracker | 28.6% |
     | passff1 | 25.7% |
     | qv-70-100 | 22.9% |
     | original | 15.7% |

   - Against `simple` bots the fascist team is about the same: 80% with the old settings, 77% with qv-55-90.
   - **Applied a compromise:** `f_vote_mate_yes=0.6`, `f_vote_libgov_yes=0.9`. The "yes for the Dictator as chancellor after 3F" rule still always applies.
   - Possible next step: vote yes for a teammate only when our vote is likely to be pivotal.
5. ~~Clue A/B test~~ **Done (2026-10-23):** threat-aware won 14/40 four-player games against 3 non-aware copies (fair share 10); all games ended solved. Kept `threat_aware=True`. Optionally re-check with 3 and 5 players.
6. ~~Re-run the fixed Connect Four test~~ **Done:** `ConnectFourTests` pass. Run the full suite once more before submitting (~6 min).
7. `WRITEUP.md` **draft written.** Update its results section with final numbers before submitting.
8. **Zip verified (2026-10-23):** 24 files, ~45 KB, within limits, without the Connect Four book. Extracted to a clean directory and run with `python -I`, it answers the docs' example Connect Four and Secret Dictator requests correctly. Rebuild with `python tools/build_zip.py` (writes `dist/submission.zip`) after any change, and again once `book.txt` exists. Upload it, and run one practice match per game with the real relay (watch the $2/day cap).
9. Optional improvements:
   - Secret Dictator: smarter liberal discussion (call out vote patterns), and dictator play after 3 fascist policies.
   - Clue: account for which card a refuter chooses to show; plan suggestions that pull opponents' pawns.

## Running things without maxing out the CPU
All sims and generators call `tools/lowprio.set_low_priority()`, which sets the Windows BELOW_NORMAL priority class (inherited by child processes), so the machine stays responsive. On top of that:
- **Connect Four book:** `python tools/c4_book.py 10 2`. The default is 2 workers (earlier runs used 13). It's resumable: Ctrl+C at any time, and the cache is saved every 20 leaves. More workers finish sooner but take more CPU and RAM (~300 MB each).
- **Secret Dictator tuning:** `python tools/tune_sd.py -n 70 -j 1 --only quiet-votes,qv-55-90`. Fewer games (`-n`), one job (`-j 1`), selected variants (`--only`). 70 games give a rough signal (±5%); use 200+ only for final decisions.
- **Sims:** always set `CRUCIBLE_QUIET=1`. Use small `-n` (5–20) for smoke tests. In-process bots (`ours`) are much cheaper than `proc`.
- **Tests:** about 6 minutes on one core. To run a single class: `python -m unittest tests.test_all.SecretDictatorTests`.
- Overnight batch jobs are the best use of idle time. Start them with `start /low` (cmd) or let `lowprio` handle it.
- To stop everything: `Get-Process python | Stop-Process` (PowerShell). This also kills any other Python work you have running.

## Common commands
```
set CRUCIBLE_QUIET=1
python tools/sim_c4.py -n 5 --red proc --yellow greedy
python tools/sim_clue.py -n 10 --bots ours,example,example
python tools/sim_sd.py -n 20 --bots ours,simple,simple,simple,simple,simple,simple
python tools/mock_relay.py --port 8089   (then OPENAI_BASE_URL=http://127.0.0.1:8089/v1)
python tools/build_zip.py
```

## Conventions
- Don't commit unless asked. Generated files `tools/c4_leaf_cache.json`, `tools/c4_book.log` and `dist/` should stay out of the zip (`build_zip.py` only packs `crucible.json`, `WRITEUP.md` and `src/`).
- Any new decision path must keep a legal fallback, and must respect `budget` (call `budget.expired()` or `budget.check()` in loops).
