"""Bot wrappers used by the local simulators."""
import json
import os
import queue
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


class ProcBot:
    """Runs a bot as a subprocess speaking the Crucible line protocol."""

    def __init__(self, argv, env=None, name=None):
        self.name = name or os.path.basename(argv[-1])
        self.p = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=env, encoding="utf-8",
        )
        self.lines = queue.Queue()
        self.stderr = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _pump_err(self):
        for line in self.p.stderr:
            self.stderr.append(line.rstrip())

    def request(self, msg, timeout):
        try:
            self.p.stdin.write(json.dumps(msg) + "\n")
            self.p.stdin.flush()
        except OSError:
            return None, 0.0
        t0 = time.monotonic()
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return None, time.monotonic() - t0
        if line is None:
            return None, time.monotonic() - t0
        try:
            return json.loads(line), time.monotonic() - t0
        except Exception:
            return None, time.monotonic() - t0

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.p.kill()


class InProcBot:
    """Calls an Agent class directly (fast, no protocol layer)."""

    def __init__(self, agent_cls, seat, soft=20.0, name="ours"):
        from common.timer import Budget
        self.Budget = Budget
        self.agent = agent_cls(seat)
        self.soft = soft
        self.name = name
        self.stderr = []

    def request(self, msg, timeout):
        if msg.get("type") == "init":
            return {}, 0.0
        t0 = time.monotonic()
        try:
            r = self.agent.decide(msg, self.Budget(self.soft))
        except Exception as e:
            import traceback
            self.stderr.append(traceback.format_exc())
            try:
                r = self.agent.fallback(msg)
            except Exception:
                r = None
        return r, time.monotonic() - t0

    def close(self):
        pass


def make_bot(spec, seat, game_cls_loader=None, soft=20.0):
    """spec: 'ours' (in-process), 'proc' (src/main.py subprocess), or a path to a bot script."""
    if spec == "ours":
        return InProcBot(game_cls_loader(), seat, soft=soft)
    if spec == "proc":
        return ProcBot([sys.executable, os.path.join(ROOT, "src", "main.py")], name="proc")
    return ProcBot([sys.executable, spec], name=os.path.basename(spec))
