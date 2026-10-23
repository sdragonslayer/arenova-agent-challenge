"""Deadline bookkeeping. All times are time.monotonic() seconds."""
import time


class OutOfTime(Exception):
    pass


class Budget:
    def __init__(self, seconds):
        self.start = time.monotonic()
        self.end = self.start + max(0.0, seconds)

    def remaining(self):
        return self.end - time.monotonic()

    def expired(self):
        return time.monotonic() >= self.end

    def check(self):
        if time.monotonic() >= self.end:
            raise OutOfTime()

    def sub(self, seconds):
        """A child budget that never outlives this one."""
        b = Budget(0)
        b.start = time.monotonic()
        b.end = min(self.end, b.start + max(0.0, seconds))
        return b
