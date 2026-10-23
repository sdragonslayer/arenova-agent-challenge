"""stdin/stdout protocol helpers and a log budget (runner keeps 200 lines x 4000 chars)."""
import json
import os
import sys
import threading

QUIET = bool(os.environ.get("CRUCIBLE_QUIET"))

_out_lock = threading.Lock()
_log_lock = threading.Lock()
_log_lines = 0
LOG_LINE_BUDGET = 180
LOG_CHAR_LIMIT = 3900


def write_reply(obj):
    data = json.dumps(obj, separators=(",", ":"))
    with _out_lock:
        sys.stdout.write(data + "\n")
        sys.stdout.flush()


def log(*parts, low_priority=False):
    """Write one line to stderr, respecting the runner's capture limits.

    Low-priority lines stop early so errors late in a match still get through.
    """
    global _log_lines
    if QUIET:
        return
    with _log_lock:
        limit = LOG_LINE_BUDGET // 2 if low_priority else LOG_LINE_BUDGET
        if _log_lines >= limit:
            return
        _log_lines += 1
        text = " ".join(str(p) for p in parts).replace("\n", " | ")
        if len(text) > LOG_CHAR_LIMIT:
            text = text[:LOG_CHAR_LIMIT]
        try:
            sys.stderr.write(text + "\n")
            sys.stderr.flush()
        except Exception:
            pass
