"""Run SD self-play tournaments for several fascist-parameter variants in parallel.

Usage: python tools/tune_sd.py -n 140 -j 4 --bots ours*7
Variants are defined in VARIANTS below (deltas from policy.PARAMS).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VARIANTS = {
    "quiet-votes": {"f_vote_mate_yes": 0.7, "f_vote_libgov_yes": 0.85},
    "qv-55-90": {"f_vote_mate_yes": 0.55, "f_vote_libgov_yes": 0.9},
    "qv-70-100": {"f_vote_mate_yes": 0.7, "f_vote_libgov_yes": 1.0},
    "qv+early-agg": {"f_vote_mate_yes": 0.7, "f_vote_libgov_yes": 0.85, "f_chanc_F_early": 0.75},
    "qv+passff1": {"f_vote_mate_yes": 0.7, "f_vote_libgov_yes": 0.85, "f_pres_pass_ff": 1.0},
    "qv+tracker": {"f_vote_mate_yes": 0.7, "f_vote_libgov_yes": 0.85, "f_vote_tracker2_no": 0.3},
}


def run(name, delta, n, bots, seed):
    env = dict(os.environ, SD_PARAMS=json.dumps(delta), CRUCIBLE_QUIET="1")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sim_sd.py"), "-n", str(n),
                          "--seed", str(seed), "--bots", bots], capture_output=True, text=True, env=env)
    rows = {}
    for line in out.stdout.splitlines():
        m = re.match(r"\s+(\S+)\s+(\S+)\s+win\s+(\d+)/\s*(\d+)", line)
        if m:
            rows[(m.group(1), m.group(2))] = (int(m.group(3)), int(m.group(4)))
    return name, rows, out.stderr[-500:]


def main():
    from lowprio import set_low_priority
    set_low_priority()
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=140)
    ap.add_argument("-j", type=int, default=2)
    ap.add_argument("--bots", default=",".join(["ours"] * 7))
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    items = [(k, v) for k, v in VARIANTS.items() if not args.only or k in args.only.split(",")]
    with ThreadPoolExecutor(args.j) as ex:
        futs = [ex.submit(run, k, v, args.n, args.bots, args.seed) for k, v in items]
        for f in futs:
            name, rows, err = f.result()
            fas = [rows.get((s, r), (0, 0)) for s in ("ours",) for r in ("fascist", "dictator")]
            w = sum(a for a, b in fas)
            t = sum(b for a, b in fas)
            detail = " ".join("%s/%s=%d/%d" % (s, r, a, b) for (s, r), (a, b) in sorted(rows.items()))
            print("%-12s ours-fascist-team %.3f  | %s" % (name, w / t if t else 0, detail), flush=True)
            if not rows:
                print("   err:", err)


if __name__ == "__main__":
    main()
