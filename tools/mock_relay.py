"""Mock OpenAI-compatible relay for offline testing of the LLM paths.

Usage: python tools/mock_relay.py [--port 8089] [--delay 0.5] [--fail-rate 0.0]
Then: OPENAI_BASE_URL=http://127.0.0.1:8089/v1 python tools/sim_sd.py ...
Claim-extraction requests are answered with the regex parser's output;
phrasing requests echo the points as one sentence.
"""
import argparse
import json
import os
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from sd.claims import parse_drawn, parse_received, parse_investigation  # noqa: E402

ARGS = None
CALLS = {"n": 0}


def answer(messages):
    system = messages[0]["content"] if messages else ""
    user = messages[-1]["content"] if messages else ""
    if "extract structured claims" in system:
        data = json.loads(user)
        out = []
        for st in data["statements"]:
            item = {"id": st["id"], "drawn_l": None, "received_l": None, "investigation": None}
            if st.get("speaker_role") == "president":
                item["drawn_l"] = parse_drawn(st["text"])
            if st.get("speaker_role") == "chancellor":
                item["received_l"] = parse_received(st["text"])
            if st.get("investigated"):
                item["investigation"] = parse_investigation(st["text"], st["investigated"])
            out.append(item)
        return json.dumps({"claims": out})
    points = [l[2:] for l in user.splitlines() if l.startswith("- ")]
    return " ".join(points)[:300]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        CALLS["n"] += 1
        time.sleep(ARGS.delay)
        if random.random() < ARGS.fail_rate:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"mock failure"}')
            return
        text = answer(body.get("messages") or [])
        resp = {"choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": len(json.dumps(body)) // 4, "completion_tokens": len(text) // 4}}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--fail-rate", type=float, default=0.0)
    ARGS = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    print("mock relay on", ARGS.port, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
