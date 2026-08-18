#!/usr/bin/env python3
"""Local dev server for the vOICe simulator.

Serves this repo (web/, stimuli/) exactly like `python3 -m http.server`,
plus one extra endpoint the browser uses to save a finished run straight
to disk:

    POST /api/save-run   body: {"filename": "...", "csv": "..."}
    -> writes test_data/<sanitized filename>, or IR_VOICE_TEST_DATA_DIR when set

test_data/ is gitignored, so results never end up in the (public) repo.
Binds to localhost only -- not reachable from the rest of the LAN.
"""
import http.server
import json
import os
import re
import socketserver
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TEST_DATA_DIR = os.environ.get(
    "IR_VOICE_TEST_DATA_DIR",
    os.path.join(ROOT, "test_data"),
)
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
MAX_BODY_BYTES = 20_000_000  # 20MB sanity cap; a run's CSV is a few KB.


# The page is edited often during development, and a stale cached copy of
# index.html/app.js silently running old logic (missing metrics, missing
# buttons) is worse than a slower load -- so never let the browser cache
# these two. Stimuli (wav/png) don't change once generated, so those keep
# normal caching for load performance.
NO_CACHE_PATHS = ("/web/index.html", "/web/app.js", "/", "")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        path = self.path.split("?")[0]
        if path in NO_CACHE_PATHS or path.endswith((".html", ".js", ".mjs")):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def do_POST(self):
        if self.path != "/api/save-run":
            self.send_error(404, "Unknown endpoint")
            return

        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_error(400, "Bad content length")
            return
        body = self.rfile.read(length)

        try:
            payload = json.loads(body)
            filename = payload["filename"]
            csv_text = payload["csv"]
        except (KeyError, json.JSONDecodeError):
            self.send_error(400, "Expected JSON {filename, csv}")
            return

        # Never trust the client's filename for path components.
        safe_name = SAFE_NAME.sub("_", os.path.basename(filename)) or "run"
        if not safe_name.endswith(".csv"):
            safe_name += ".csv"

        os.makedirs(TEST_DATA_DIR, exist_ok=True)
        dest = os.path.join(TEST_DATA_DIR, safe_name)
        with open(dest, "w", newline="") as f:
            f.write(csv_text)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"saved": True, "path": dest}).encode())


class LocalTCPServer(socketserver.TCPServer):
    # A packaged app may be closed and reopened immediately. macOS can retain
    # the previous listener in TIME_WAIT briefly, so permit the same local port
    # to be rebound without making the launcher appear broken.
    allow_reuse_address = True


if __name__ == "__main__":
    os.makedirs(TEST_DATA_DIR, exist_ok=True)
    with LocalTCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Serving {ROOT} at http://localhost:{PORT}")
        print(f"POST /api/save-run writes into {TEST_DATA_DIR}")
        httpd.serve_forever()
