"""Statements: decide *what* to say deterministically, optionally let the LLM phrase it.

The LLM never sees our hidden role and never decides content; it only rewrites
a list of points, and its output is rejected unless it keeps the factual claim
string verbatim and fits the 320-character limit.
"""
from common.io import log

MAX_LEN = 320


def code(cards_l, n):
    """e.g. code(1, 3) -> 'FFL' (liberal count first-normalised as F..L)."""
    return "F" * (n - cards_l) + "L" * cards_l


def words(cards_l, n):
    f = n - cards_l
    parts = []
    if f:
        parts.append("%d Fascist" % f)
    if cards_l:
        parts.append("%d Liberal" % cards_l)
    return " + ".join(parts)


def president_claim(drawn_l, passed_l):
    disc = "Fascist" if (drawn_l - passed_l) == 0 else "Liberal"
    return ("As President I drew %s (%s), discarded a %s and passed %s."
            % (code(drawn_l, 3), words(drawn_l, 3), disc, code(passed_l, 2)))


def chancellor_claim(received_l, enacted):
    return "As Chancellor I received %s (%s) and enacted %s." % (
        code(received_l, 2), words(received_l, 2), enacted.capitalize())


def clip(text):
    text = " ".join(text.split())
    if len(text) <= MAX_LEN:
        return text
    cut = text[:MAX_LEN - 1]
    if "." in cut[100:]:
        cut = cut[:cut.rfind(".") + 1]
    return cut.strip()


class Talker:
    def __init__(self, llm=None):
        self.llm = llm

    def render(self, points, keep=None, budget_s=0.0, style_hint=None):
        """points: list of sentences. keep: substring that must survive LLM phrasing."""
        base = clip(" ".join(p for p in points if p))
        if not base:
            return None
        if self.llm is None or not self.llm.available or budget_s < 6.0:
            return base
        system = (
            "You write one short table-talk message for a social deduction game (Secret Hitler rules; "
            "the Hitler role is called the Dictator). Rewrite the given points as a natural, confident, "
            "concise message of at most 260 characters. Keep every factual claim; do not add new facts, "
            "accusations or numbers. Copy any text in KEEP exactly. Output only the message."
        )
        user = "POINTS:\n- " + "\n- ".join(points)
        if keep:
            user += "\nKEEP: " + keep
        if style_hint:
            user += "\nTONE: " + style_hint
        out = self.llm.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            timeout=min(8.0, budget_s - 2.0), max_tokens=160, temperature=0.6,
        )
        if not out:
            return base
        out = out.strip().strip('"').strip()
        if keep and keep not in out:
            log("talk: llm dropped claim; using template")
            return base
        if not out or len(out) > MAX_LEN:
            return base
        return out
