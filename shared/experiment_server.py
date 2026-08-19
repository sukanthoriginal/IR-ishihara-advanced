"""Local-only static server, session builder, and CSV persistence endpoint."""

from __future__ import annotations

import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from advanced_ishihara.generate_session import prepare_session

SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
MAX_BODY_BYTES = 20_000_000


def make_handler(repo_root: Path, test_data_dir: Path, session_dir: Path):
    repo_root = repo_root.resolve()
    session_root = session_dir.resolve()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=repo_root, **kwargs)

        def end_headers(self):
            path = urlparse(self.path).path
            if (
                path.endswith((".html", ".js", ".mjs", "manifest.json"))
                or path in {"/", "/advanced/"}
            ):
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
            super().end_headers()

        def do_GET(self):
            if urlparse(self.path).path == "/":
                self.send_response(302)
                self.send_header("Location", "/advanced/")
                self.end_headers()
                return
            super().do_GET()

        def do_HEAD(self):
            if urlparse(self.path).path == "/":
                self.send_response(302)
                self.send_header("Location", "/advanced/")
                self.end_headers()
                return
            super().do_HEAD()

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/save-run":
                self._save_run()
            elif path == "/api/prepare-session":
                self._prepare_session()
            else:
                self.send_error(404, "Unknown endpoint")

        def translate_path(self, path: str) -> str:
            request_path = unquote(urlparse(path).path)
            prefix = "/advanced_sessions/"
            if request_path.startswith(prefix):
                relative = Path(request_path[len(prefix):])
                candidate = (session_root / relative).resolve()
                try:
                    candidate.relative_to(session_root)
                except ValueError:
                    return str(session_root / "__invalid_path__")
                return str(candidate)
            return super().translate_path(path)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("bad content length")
            try:
                payload = json.loads(self.rfile.read(length))
            except json.JSONDecodeError as error:
                raise ValueError("invalid JSON") from error
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _save_run(self):
            try:
                payload = self._read_json()
                filename = str(payload["filename"])
                csv_text = str(payload["csv"])
            except (KeyError, ValueError) as error:
                self._write_json(400, {"error": str(error)})
                return
            safe_name = SAFE_NAME.sub("_", os.path.basename(filename)) or "run"
            if not safe_name.endswith(".csv"):
                safe_name += ".csv"
            test_data_dir.mkdir(parents=True, exist_ok=True)
            destination = test_data_dir / safe_name
            destination.write_text(csv_text, newline="")
            self._write_json(200, {"saved": True, "path": str(destination)})

        def _prepare_session(self):
            try:
                payload = self._read_json()
                manifest_path, manifest = prepare_session(
                    payload,
                    session_dir,
                    repo_root,
                )
            except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
                self._write_json(400, {"error": str(error)})
                return
            relative_path = manifest_path.relative_to(session_root)
            self._write_json(200, {
                "sessionId": manifest["session_id"],
                "manifestUrl": "/advanced_sessions/" + relative_path.as_posix(),
                "audioGenerated": manifest["audio_generated"],
                "trialCount": len(manifest["trials"]),
            })

        def _write_json(self, status: int, payload: dict):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


class LocalThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(repo_root: Path | None = None) -> None:
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    test_data_dir = Path(os.environ.get(
        "ADVANCED_ISHIHARA_TEST_DATA_DIR",
        root / "test_data",
    ))
    session_dir = Path(os.environ.get(
        "ADVANCED_ISHIHARA_SESSION_DIR",
        root / "advanced_sessions",
    ))
    handler = make_handler(root, test_data_dir, session_dir)
    with LocalThreadingServer(("127.0.0.1", port), handler) as server:
        print(f"Advanced IR-Ishihara: http://127.0.0.1:{port}/advanced/")
        print(f"CSV output: {test_data_dir}")
        print(f"Session cache: {session_dir}")
        server.serve_forever()
