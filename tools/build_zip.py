"""Package the submission ZIP and check Crucible's limits.

Usage: python tools/build_zip.py [out.zip]
"""
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCLUDE_FILES = ["crucible.json", "WRITEUP.md"]
INCLUDE_DIRS = ["src"]
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_EXT = {".pyc", ".pyo"}

LIMIT_ZIP = 2 * 1024 * 1024
LIMIT_EXPANDED = 12 * 1024 * 1024
LIMIT_FILE = 4 * 1024 * 1024
LIMIT_COUNT = 250


def files():
    for f in INCLUDE_FILES:
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            yield p, f
    for d in INCLUDE_DIRS:
        for base, dirs, names in os.walk(os.path.join(ROOT, d)):
            dirs[:] = [x for x in dirs if x not in SKIP_PARTS]
            for n in sorted(names):
                if os.path.splitext(n)[1] in SKIP_EXT:
                    continue
                p = os.path.join(base, n)
                yield p, os.path.relpath(p, ROOT).replace(os.sep, "/")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "submission.zip")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(os.path.join(ROOT, "crucible.json")) as f:
        cfg = json.load(f)
    assert os.path.exists(os.path.join(ROOT, cfg["entrypoint"])), "entrypoint missing"
    total = 0
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path, arc in files():
            size = os.path.getsize(path)
            assert size <= LIMIT_FILE, "%s exceeds 4 MB" % arc
            total += size
            count += 1
            z.write(path, arc)
    zsize = os.path.getsize(out)
    print("wrote %s: %d files, %.1f KB expanded, %.1f KB zipped" % (out, count, total / 1024, zsize / 1024))
    assert count <= LIMIT_COUNT, "too many files"
    assert total <= LIMIT_EXPANDED, "expanded size too large"
    assert zsize <= LIMIT_ZIP, "zip too large"
    print("limits OK")


if __name__ == "__main__":
    main()
