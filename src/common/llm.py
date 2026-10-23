"""Minimal OpenAI-compatible chat client for the Crucible relay (stdlib only).

Every failure mode returns None so callers can fall back to deterministic logic.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

from common.io import log


class LLM:
    def __init__(self, max_calls=80, max_failures=6):
        self.base_url = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("CRUCIBLE_AI_MODEL", "openai/gpt-6-luna")
        self.max_calls = max_calls
        self.max_failures = max_failures
        self.calls = 0
        self.failures = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self._lock = threading.Lock()

    @property
    def available(self):
        return (
            bool(self.base_url)
            and self.calls < self.max_calls
            and self.failures < self.max_failures
        )

    def chat(self, messages, timeout=20.0, max_tokens=400, temperature=0.7, json_mode=False):
        """Return the assistant text or None."""
        with self._lock:
            if not self.available or timeout < 1.0:
                return None
            self.calls += 1
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        text = self._post(body, timeout)
        if text is None and json_mode:
            # Some relays reject response_format; retry once without it if time allows.
            body.pop("response_format", None)
            left = timeout - (time.monotonic() - started)
            if left > 3.0:
                text = self._post(body, left)
        if text is None:
            with self._lock:
                self.failures += 1
        return text

    def _post(self, body, timeout):
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            log("llm http error", e.code, detail)
            return None
        except Exception as e:  # timeout, connection refused, bad JSON...
            log("llm error", type(e).__name__, str(e)[:200])
            return None
        try:
            usage = payload.get("usage") or {}
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)
            return payload["choices"][0]["message"]["content"] or None
        except Exception:
            return None


def extract_json(text):
    """Pull the first JSON object/array out of a model reply."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                continue
    return None
