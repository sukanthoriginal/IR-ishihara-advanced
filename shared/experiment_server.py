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

from advanced_ishihara.generate_session import load_grammar, prepare_session
from shared.local_state import (
    ActiveSessionLeaseError,
    LocalParticipantState,
    RepeatThresholdError,
    audit_participant_candidate,
    normalize_ledger_text,
    normalize_participant_id,
    normalize_signature_digest,
    plan_participant_session,
    signature_digest,
)

SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
MAX_BODY_BYTES = 20_000_000


def make_handler(
    repo_root: Path,
    test_data_dir: Path,
    session_dir: Path,
    mirror_data_dir: Path | None = None,
):
    repo_root = repo_root.resolve()
    session_root = session_dir.resolve()
    mirror_root = mirror_data_dir.resolve() if mirror_data_dir else None
    local_state = LocalParticipantState(test_data_dir, test_data_dir, repo_root)

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
            if not self._has_local_host():
                self.send_error(403, "Local host required")
                return
            path = urlparse(self.path).path
            if path == "/api/local-state":
                self._local_state()
                return
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/advanced/")
                self.end_headers()
                return
            super().do_GET()

        def do_HEAD(self):
            if not self._has_local_host():
                self.send_error(403, "Local host required")
                return
            if urlparse(self.path).path == "/":
                self.send_response(302)
                self.send_header("Location", "/advanced/")
                self.end_headers()
                return
            super().do_HEAD()

        def do_POST(self):
            if not self._is_local_mutation_request():
                self._write_json(403, {"error": "Local same-origin request required"})
                return
            path = urlparse(self.path).path
            if path == "/api/save-run":
                self._save_run()
            elif path == "/api/prepare-session":
                self._prepare_session()
            elif path == "/api/preferences":
                self._preferences()
            elif path == "/api/participants":
                self._register_participant()
            elif path == "/api/revalidate-session":
                self._revalidate_session()
            elif path == "/api/release-session":
                self._release_session()
            elif path == "/api/force-release-session":
                self._force_release_session()
            elif path == "/api/record-exposure":
                self._record_exposure()
            else:
                self.send_error(404, "Unknown endpoint")

        def translate_path(self, path: str) -> str:
            request_path = unquote(urlparse(path).path)
            static_candidate = (
                repo_root / request_path.lstrip("/")
            ).resolve()
            try:
                static_candidate.relative_to(local_state.state_directory)
            except ValueError:
                pass
            else:
                # The source server's document root is the repository. Never
                # expose its ignored participant database or result CSVs.
                return str(repo_root / "__private_local_data__")
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
                participant_id = normalize_participant_id(payload["participantId"])
                session_id = normalize_ledger_text(
                    payload["sessionId"], "sessionId", 240,
                )
                preparation_id = payload["preparationId"]
                candidate_digest = normalize_signature_digest(
                    payload["candidateSignatureDigest"],
                )
                save_directory = local_state.session_save_directory(
                    participant_id, session_id, preparation_id, candidate_digest,
                )
            except (KeyError, ValueError) as error:
                self._write_json(400, {"error": str(error)})
                return
            safe_name = SAFE_NAME.sub("_", os.path.basename(filename)) or "run"
            if not safe_name.endswith(".csv"):
                safe_name += ".csv"
            try:
                destination = self._write_csv_without_overwrite(
                    save_directory, safe_name, csv_text,
                )
                mirror_destination = self._write_csv_mirror(
                    mirror_root, destination, csv_text,
                )
            except OSError as error:
                self._write_json(500, {
                    "error": f"Could not save every CSV copy: {error}",
                })
                return
            response = {
                "saved": True,
                "path": str(destination),
                "paths": [str(destination)],
                "saveDirectory": str(save_directory),
            }
            if mirror_destination is not None:
                response["mirrorPath"] = str(mirror_destination)
                response["paths"].append(str(mirror_destination))
            self._write_json(200, response)

        def _prepare_session(self):
            try:
                payload = self._read_json()
                participant_id = normalize_participant_id(
                    payload.get("participantId", ""),
                )
                requested_save_directory = payload.get(
                    "resultsDirectory", payload.get("saveDirectory"),
                )
                selected_save_directory = local_state.validate_save_directory(
                    requested_save_directory,
                )
                local_state.update_preferences(
                    participant_id=participant_id,
                    save_directory=selected_save_directory,
                )
                history = local_state.history_signatures(participant_id)
                effective_settings, audit = plan_participant_session(
                    payload,
                    repo_root,
                    participant_id,
                    history,
                )
                manifest_path, manifest = prepare_session(
                    effective_settings,
                    session_dir,
                    repo_root,
                )
            except RepeatThresholdError as error:
                self._write_json(409, {
                    "error": str(error),
                    "randomizationAudit": error.audit,
                })
                return
            except OSError as error:
                self._write_json(500, {
                    "error": f"Could not build session assets: {error}",
                })
                return
            except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
                self._write_json(400, {"error": str(error)})
                return
            manifest_signatures = [
                item["transformation_signature"] for item in manifest["stimuli"]
            ]
            manifest_digest = signature_digest(manifest_signatures)
            if (
                len(set(manifest_signatures))
                != audit["candidateUniqueTransformations"]
                or manifest_digest != audit["candidateSignatureDigest"]
            ):
                self._write_json(500, {
                    "error": "Prepared manifest did not match its randomization audit",
                })
                return
            try:
                binding = local_state.create_prepared_session_binding(
                    participant_id,
                    manifest["session_id"],
                    manifest_digest,
                    audit["requestedSeed"],
                    audit["effectiveSeed"],
                    audit["rerandomizations"],
                    selected_save_directory,
                )
            except ValueError as error:
                self._write_json(500, {"error": str(error)})
                return
            relative_path = manifest_path.relative_to(session_root)
            self._write_json(200, {
                "sessionId": manifest["session_id"],
                "manifestUrl": "/advanced_sessions/" + relative_path.as_posix(),
                "audioGenerated": manifest["audio_generated"],
                "trialCount": len(manifest["trials"]),
                "randomizationAudit": audit,
                "preparationId": binding["preparationId"],
                "saveDirectory": binding["saveDirectory"],
            })

        def _local_state(self):
            self._write_json(200, self._local_state_payload())

        def _local_state_payload(self):
            state = local_state.preferences()
            participant_id = state["participantId"]
            state["participantUniqueSeen"] = (
                len(local_state.history_signatures(participant_id))
                if participant_id else 0
            )
            state["participants"] = local_state.participants()
            return state

        def _preferences(self):
            try:
                payload = self._read_json()
                requested_directory = payload.get(
                    "saveDirectory", payload.get("resultsDirectory"),
                )
                local_state.update_preferences(
                    participant_id=payload.get("participantId"),
                    save_directory=requested_directory,
                )
            except ValueError as error:
                self._write_json(400, {"error": str(error)})
                return
            self._write_json(200, self._local_state_payload())

        def _register_participant(self):
            try:
                payload = self._read_json()
                participant_id = normalize_participant_id(payload["participantId"])
                local_state.update_preferences(participant_id=participant_id)
            except (KeyError, ValueError) as error:
                self._write_json(400, {"error": str(error)})
                return
            self._write_json(200, self._local_state_payload())

        def _record_exposure(self):
            try:
                payload = self._read_json()
                participant_id = normalize_participant_id(
                    payload.get("participantId", ""),
                )
                session_id = normalize_ledger_text(
                    payload.get("sessionId", ""), "sessionId", 240,
                )
                preparation_id = payload.get("preparationId", "")
                stimulus_id = normalize_ledger_text(
                    payload.get("stimulusId", ""), "stimulusId", 240,
                )
                signature = normalize_ledger_text(
                    payload.get("transformationSignature", ""),
                    "transformationSignature",
                    4096,
                )
                local_state.prepared_session_binding(
                    participant_id, session_id, preparation_id,
                )
                manifest = self._load_session_manifest(session_id)
                stimulus = next(
                    (
                        item for item in manifest.get("stimuli", [])
                        if item.get("stimulus_id") == stimulus_id
                    ),
                    None,
                )
                if stimulus is None:
                    raise ValueError("Stimulus is not part of this session")
                if stimulus.get("transformation_signature") != signature:
                    raise ValueError("Transformation signature does not match stimulus")
                result = local_state.record_active_exposure(
                    participant_id,
                    session_id,
                    preparation_id,
                    stimulus_id,
                    signature,
                )
            except ActiveSessionLeaseError as error:
                self._write_json(409, {
                    "error": str(error),
                    "code": error.code,
                    "activeSessionId": error.active_session_id,
                    "activePreparationId": error.active_preparation_id,
                })
                return
            except (OSError, json.JSONDecodeError, ValueError) as error:
                self._write_json(400, {"error": str(error)})
                return
            self._write_json(200, result)

        def _revalidate_session(self):
            try:
                payload = self._read_json()
                participant_id = normalize_participant_id(payload["participantId"])
                session_id = normalize_ledger_text(
                    payload["sessionId"], "sessionId", 240,
                )
                preparation_id = payload["preparationId"]
                supplied_digest = normalize_signature_digest(
                    payload["candidateSignatureDigest"],
                )
                binding = local_state.prepared_session_binding(
                    participant_id, session_id, preparation_id,
                )
                if supplied_digest != binding["candidateSignatureDigest"]:
                    raise ValueError(
                        "Candidate signature digest does not match prepared session"
                    )
                manifest = self._load_session_manifest(session_id)
                signatures = [
                    item["transformation_signature"]
                    for item in manifest.get("stimuli", [])
                ]
                manifest_digest = signature_digest(signatures)
                if manifest_digest != supplied_digest:
                    raise ValueError(
                        "Prepared manifest signature digest does not match binding"
                    )
                if manifest.get("settings", {}).get("seed") != binding["effectiveSeed"]:
                    raise ValueError("Prepared manifest seed does not match binding")
                grammar = load_grammar(repo_root)
                audit, session_lease = local_state.audit_and_claim_session(
                    participant_id,
                    session_id,
                    preparation_id,
                    lambda current_history: audit_participant_candidate(
                        manifest["settings"],
                        repo_root,
                        participant_id,
                        current_history,
                        signatures,
                        binding["requestedSeed"],
                        binding["rerandomizations"],
                        grammar=grammar,
                    ),
                )
            except ActiveSessionLeaseError as error:
                self._write_json(409, {
                    "error": str(error),
                    "code": error.code,
                    "activeSessionId": error.active_session_id,
                    "activePreparationId": error.active_preparation_id,
                })
                return
            except (KeyError, OSError, json.JSONDecodeError, ValueError) as error:
                self._write_json(409, {"error": str(error)})
                return
            self._write_json(200, {
                "sessionId": session_id,
                "preparationId": binding["preparationId"],
                "randomizationAudit": audit,
                "sessionLease": session_lease,
            })

        def _release_session(self):
            try:
                payload = self._read_json()
                participant_id = normalize_participant_id(payload["participantId"])
                session_id = normalize_ledger_text(
                    payload["sessionId"], "sessionId", 240,
                )
                preparation_id = payload["preparationId"]
                local_state.prepared_session_binding(
                    participant_id, session_id, preparation_id,
                )
                released = local_state.release_session(
                    participant_id, session_id, preparation_id,
                )
            except (KeyError, ValueError) as error:
                self._write_json(400, {"error": str(error)})
                return
            self._write_json(200, {
                "sessionId": session_id,
                "preparationId": preparation_id,
                "released": released,
            })

        def _force_release_session(self):
            try:
                payload = self._read_json()
                participant_id = normalize_participant_id(payload["participantId"])
                if payload.get("confirmAbandonedSession") is not True:
                    self._write_json(400, {
                        "error": "Explicit abandoned-session confirmation is required",
                        "code": "confirmation_required",
                    })
                    return
                result = local_state.force_release_participant_session(
                    participant_id,
                    payload["expectedSessionId"],
                    payload["expectedPreparationId"],
                )
            except (KeyError, ValueError) as error:
                self._write_json(400, {"error": str(error), "code": "invalid_request"})
                return
            self._write_json(200, result)

        def _load_session_manifest(self, session_id: str) -> dict:
            manifest_path = (session_root / session_id / "manifest.json").resolve()
            manifest_path.relative_to(session_root)
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("session_id") != session_id:
                raise ValueError("Session manifest does not match sessionId")
            return manifest

        def _is_local_mutation_request(self) -> bool:
            if not self._has_local_host():
                return False
            port = self.server.server_address[1]
            origin_value = self.headers.get("Origin")
            if not origin_value:
                return True
            origin = urlparse(origin_value)
            if origin.scheme != "http":
                return False
            if origin.hostname not in {"127.0.0.1", "localhost", "::1"}:
                return False
            try:
                return (origin.port or 80) == port
            except ValueError:
                return False

        def _has_local_host(self) -> bool:
            port = self.server.server_address[1]
            host = urlparse("//" + self.headers.get("Host", ""))
            if host.hostname not in {"127.0.0.1", "localhost", "::1"}:
                return False
            try:
                return (host.port or 80) == port
            except ValueError:
                return False

        @staticmethod
        def _write_csv_without_overwrite(
            save_directory: Path,
            safe_name: str,
            csv_text: str,
        ) -> Path:
            requested = Path(safe_name)
            stem = requested.stem or "run"
            suffix = requested.suffix or ".csv"
            for collision_index in range(10_000):
                filename = (
                    safe_name if collision_index == 0
                    else f"{stem}_{collision_index + 1}{suffix}"
                )
                destination = save_directory / filename
                try:
                    with destination.open("x", newline="") as output:
                        output.write(csv_text)
                    return destination
                except FileExistsError:
                    continue
            raise OSError("too many files use this result filename")

        @staticmethod
        def _write_csv_mirror(
            mirror_root: Path | None,
            source_destination: Path,
            csv_text: str,
        ) -> Path | None:
            if mirror_root is None or mirror_root == source_destination.parent.resolve():
                return None
            mirror_root.mkdir(parents=True, exist_ok=True)
            destination = mirror_root / source_destination.name
            try:
                with destination.open("x", newline="") as output:
                    output.write(csv_text)
            except FileExistsError:
                if destination.read_text() != csv_text:
                    raise OSError(
                        f"mirror filename collision with different contents: {destination}",
                    )
            return destination

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
    mirror_data_value = os.environ.get("ADVANCED_ISHIHARA_MIRROR_DATA_DIR")
    mirror_data_dir = Path(mirror_data_value) if mirror_data_value else None
    session_dir = Path(os.environ.get(
        "ADVANCED_ISHIHARA_SESSION_DIR",
        root / "advanced_sessions",
    ))
    handler = make_handler(root, test_data_dir, session_dir, mirror_data_dir)
    with LocalThreadingServer(("127.0.0.1", port), handler) as server:
        print(f"Advanced IR-Ishihara: http://127.0.0.1:{port}/advanced/")
        print(f"CSV output: {test_data_dir}")
        if mirror_data_dir is not None:
            print(f"CSV mirror: {mirror_data_dir}")
        print(f"Session cache: {session_dir}")
        server.serve_forever()
