import json
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from advanced_ishihara.generate_session import load_grammar, plan_session
from shared.experiment_server import LocalThreadingServer, make_handler
from shared.local_state import (
    ActiveSessionLeaseError,
    HISTORICAL_REPEAT_THRESHOLD,
    LocalParticipantState,
    RepeatThresholdError,
    SESSION_LEASE_INACTIVITY_SECONDS,
    audit_participant_candidate,
    plan_participant_session,
    signature_digest,
)


ROOT = Path(__file__).resolve().parents[1]


class LocalParticipantStateTests(unittest.TestCase):
    def test_preferences_and_unique_exposure_history_persist_locally(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_root = Path(temp_name)
            repo_root = temp_root / "repo"
            data_root = repo_root / "test_data"
            result_root = temp_root / "chosen-results"
            repo_root.mkdir()
            result_root.mkdir()
            state = LocalParticipantState(data_root, data_root, repo_root)

            self.assertEqual(state.preferences()["participantId"], "")
            self.assertEqual(
                state.preferences()["saveDirectory"], str(data_root.resolve()),
            )
            remembered = state.update_preferences("P 001", result_root)
            self.assertEqual(remembered["participantId"], "P 001")
            self.assertEqual(remembered["saveDirectory"], str(result_root.resolve()))
            self.assertEqual(
                [item["participantId"] for item in state.participants()],
                ["P 001"],
            )

            first = state.record_exposure(
                "P 001", "session-a", "stimulus-1", "1--2",
            )
            duplicate = state.record_exposure(
                "P 001", "session-b", "stimulus-9", "1--2",
            )
            self.assertTrue(first["recorded"])
            self.assertFalse(duplicate["recorded"])
            self.assertEqual(duplicate["participantUniqueSeen"], 1)
            self.assertEqual(state.participants()[0]["participantUniqueSeen"], 1)
            self.assertEqual(state.history_signatures("P 001"), {"1--2"})
            self.assertEqual(state.history_signatures("another participant"), set())

            reopened = LocalParticipantState(data_root, data_root, repo_root)
            self.assertEqual(reopened.preferences()["participantId"], "P 001")
            self.assertEqual(reopened.history_signatures("P 001"), {"1--2"})
            self.assertEqual(reopened.participants()[0]["participantId"], "P 001")
            self.assertTrue((data_root / "participant_history.sqlite3").is_file())
            self.assertEqual(stat.S_IMODE(data_root.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((data_root / "participant_history.sqlite3").stat().st_mode),
                0o600,
            )

    def test_registered_participants_are_local(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_root = Path(temp_name)
            repo_root = temp_root / "repo"
            data_root = repo_root / "test_data"
            repo_root.mkdir()
            state = LocalParticipantState(data_root, data_root, repo_root)
            state.register_participant("Participant A")
            state.register_participant("Participant B")
            summaries = {item["participantId"]: item for item in state.participants()}
            self.assertEqual(set(summaries), {"Participant A", "Participant B"})
            self.assertFalse(summaries["Participant A"]["activeSession"])
            self.assertFalse(summaries["Participant B"]["activeSession"])

    def test_repository_results_must_remain_under_ignored_data_tree(self):
        with tempfile.TemporaryDirectory() as temp_name:
            repo_root = Path(temp_name) / "repo"
            data_root = repo_root / "test_data"
            unsafe = repo_root / "participant-results"
            unsafe.mkdir(parents=True)
            state = LocalParticipantState(data_root, data_root, repo_root)
            with self.assertRaisesRegex(ValueError, "under"):
                state.update_preferences(save_directory=unsafe)
            with self.assertRaisesRegex(ValueError, "absolute"):
                state.update_preferences(save_directory="relative/results")
            with self.assertRaisesRegex(ValueError, "does not exist"):
                state.update_preferences(
                    save_directory=Path(temp_name) / "missing-results",
                )

    def test_each_prepare_gets_an_immutable_save_binding(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_root = Path(temp_name)
            repo_root = temp_root / "repo"
            data_root = repo_root / "test_data"
            first_directory = temp_root / "first"
            second_directory = temp_root / "second"
            repo_root.mkdir()
            first_directory.mkdir()
            second_directory.mkdir()
            state = LocalParticipantState(data_root, data_root, repo_root)
            digest = "a" * 64
            first = state.create_prepared_session_binding(
                "P1", "same-session", digest, 1, 2, 1, first_directory,
            )
            second = state.create_prepared_session_binding(
                "P1", "same-session", digest, 1, 2, 1, second_directory,
            )
            self.assertNotEqual(first["preparationId"], second["preparationId"])
            self.assertEqual(
                state.session_save_directory(
                    "P1", "same-session", first["preparationId"], digest,
                ),
                first_directory.resolve(),
            )
            self.assertEqual(
                state.session_save_directory(
                    "P1", "same-session", second["preparationId"], digest,
                ),
                second_directory.resolve(),
            )
            with self.assertRaisesRegex(ValueError, "digest"):
                state.session_save_directory(
                    "P1", "same-session", first["preparationId"], "b" * 64,
                )

    def test_missing_results_volume_does_not_block_binding_or_history(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_root = Path(temp_name)
            repo_root = temp_root / "repo"
            data_root = repo_root / "test_data"
            removable_results = temp_root / "removable-results"
            repo_root.mkdir()
            removable_results.mkdir()
            state = LocalParticipantState(data_root, data_root, repo_root)
            digest = "c" * 64
            binding = state.create_prepared_session_binding(
                "P-volume", "volume-session", digest, 3, 3, 0,
                removable_results,
            )
            removable_results.rmdir()

            recovered = state.prepared_session_binding(
                "P-volume", "volume-session", binding["preparationId"],
            )
            self.assertEqual(
                recovered["saveDirectory"], str(removable_results.resolve()),
            )
            audit, lease = state.audit_and_claim_session(
                "P-volume", "volume-session", binding["preparationId"],
                lambda history: {"accepted": True, "historyCount": len(history)},
                now=50,
            )
            self.assertTrue(audit["accepted"])
            self.assertTrue(lease["acquired"])
            self.assertTrue(state.record_active_exposure(
                "P-volume", "volume-session", binding["preparationId"],
                "stimulus_001", "1--2", now=51,
            )["recorded"])
            with self.assertRaisesRegex(ValueError, "does not exist"):
                state.session_save_directory(
                    "P-volume", "volume-session", binding["preparationId"], digest,
                )

    def test_participant_lease_competes_atomically_and_same_claim_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_root = Path(temp_name)
            repo_root = temp_root / "repo"
            data_root = repo_root / "test_data"
            results = temp_root / "results"
            repo_root.mkdir()
            results.mkdir()
            state = LocalParticipantState(data_root, data_root, repo_root)
            first = state.create_prepared_session_binding(
                "P-lease", "session-a", "a" * 64, 1, 1, 0, results,
            )
            second = state.create_prepared_session_binding(
                "P-lease", "session-b", "b" * 64, 2, 2, 0, results,
            )
            start_gate = threading.Barrier(2)

            def compete(binding):
                start_gate.wait(timeout=5)
                try:
                    _audit, lease = state.audit_and_claim_session(
                        "P-lease",
                        binding["sessionId"],
                        binding["preparationId"],
                        lambda history: {
                            "accepted": True,
                            "historyCount": len(history),
                        },
                        now=100,
                    )
                    return lease
                except ActiveSessionLeaseError:
                    return None

            with ThreadPoolExecutor(max_workers=2) as executor:
                results_by_claim = list(executor.map(compete, (first, second)))
            winners = [lease for lease in results_by_claim if lease is not None]
            self.assertEqual(len(winners), 1)
            winner = winners[0]
            self.assertTrue(winner["acquired"])
            self.assertFalse(winner["renewed"])
            self.assertEqual(state.history_signatures("P-lease"), set())

            _audit, renewed = state.audit_and_claim_session(
                "P-lease",
                winner["sessionId"],
                winner["preparationId"],
                lambda _history: {"accepted": True},
                now=200,
            )
            self.assertFalse(renewed["acquired"])
            self.assertTrue(renewed["renewed"])

    def test_lease_renews_on_exposure_expires_and_release_checks_owner(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_root = Path(temp_name)
            repo_root = temp_root / "repo"
            data_root = repo_root / "test_data"
            repo_root.mkdir()
            state = LocalParticipantState(data_root, data_root, repo_root)
            first_preparation = "11111111-1111-4111-8111-111111111111"
            second_preparation = "22222222-2222-4222-8222-222222222222"
            state.audit_and_claim_session(
                "P-expiry", "session-a", first_preparation,
                lambda history: {"accepted": True, "history": len(history)},
                now=100,
            )
            exposure = state.record_active_exposure(
                "P-expiry", "session-a", first_preparation,
                "stimulus_001", "1--2",
                now=100 + SESSION_LEASE_INACTIVITY_SECONDS - 1,
            )
            self.assertTrue(exposure["recorded"])
            with self.assertRaises(ActiveSessionLeaseError):
                state.audit_and_claim_session(
                    "P-expiry", "session-b", second_preparation,
                    lambda _history: {"accepted": True},
                    now=100 + SESSION_LEASE_INACTIVITY_SECONDS + 1,
                )

            expiry = 100 + 2 * SESSION_LEASE_INACTIVITY_SECONDS - 1
            _audit, replacement = state.audit_and_claim_session(
                "P-expiry", "session-b", second_preparation,
                lambda history: {"accepted": True, "history": len(history)},
                now=expiry,
            )
            self.assertTrue(replacement["acquired"])
            with self.assertRaises(ActiveSessionLeaseError):
                state.record_active_exposure(
                    "P-expiry", "session-a", first_preparation,
                    "stimulus_002", "L--I", now=expiry + 1,
                )
            self.assertFalse(state.release_session(
                "P-expiry", "session-a", first_preparation,
            ))
            self.assertTrue(state.release_session(
                "P-expiry", "session-b", second_preparation,
            ))
            self.assertFalse(state.release_session(
                "P-expiry", "session-b", second_preparation,
            ))

    def test_force_release_is_explicit_and_participant_scoped(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_root = Path(temp_name)
            repo_root = temp_root / "repo"
            data_root = repo_root / "test_data"
            repo_root.mkdir()
            state = LocalParticipantState(data_root, data_root, repo_root)
            first_preparation = "11111111-1111-4111-8111-111111111111"
            second_preparation = "22222222-2222-4222-8222-222222222222"
            attempted_preparation = "33333333-3333-4333-8333-333333333333"
            replacement_preparation = "44444444-4444-4444-8444-444444444444"
            state.audit_and_claim_session(
                "P-first", "session-first", first_preparation,
                lambda _history: {"accepted": True}, now=100,
            )
            state.audit_and_claim_session(
                "P-second", "session-second", second_preparation,
                lambda _history: {"accepted": True}, now=100,
            )

            absent = state.force_release_participant_session(
                "P-absent", "session-first", first_preparation,
            )
            self.assertEqual(absent, {
                "participantId": "P-absent",
                "released": False,
                "code": "no_active_session",
                "releasedSessionId": None,
                "releasedPreparationId": None,
                "activeSessionId": None,
                "activePreparationId": None,
            })
            with self.assertRaises(ActiveSessionLeaseError) as first_active:
                state.audit_and_claim_session(
                    "P-first", "attempted", attempted_preparation,
                    lambda _history: {"accepted": True}, now=101,
                )
            self.assertEqual(
                first_active.exception.code, "participant_session_active",
            )
            self.assertEqual(
                first_active.exception.active_session_id, "session-first",
            )
            self.assertEqual(
                first_active.exception.active_preparation_id, first_preparation,
            )
            with self.assertRaises(ActiveSessionLeaseError):
                state.audit_and_claim_session(
                    "P-second", "attempted", attempted_preparation,
                    lambda _history: {"accepted": True}, now=101,
                )

            # The observed owner exits normally, then a new preparation claims
            # the participant before the stale recovery action arrives.
            self.assertTrue(state.release_session(
                "P-first", "session-first", first_preparation,
            ))
            _audit, replacement = state.audit_and_claim_session(
                "P-first", "replacement", replacement_preparation,
                lambda _history: {"accepted": True}, now=102,
            )
            self.assertTrue(replacement["acquired"])
            changed = state.force_release_participant_session(
                "P-first",
                first_active.exception.active_session_id,
                first_active.exception.active_preparation_id,
            )
            self.assertEqual(changed, {
                "participantId": "P-first",
                "released": False,
                "code": "active_session_changed",
                "releasedSessionId": None,
                "releasedPreparationId": None,
                "activeSessionId": "replacement",
                "activePreparationId": replacement_preparation,
            })
            with self.assertRaises(ActiveSessionLeaseError) as replacement_active:
                state.audit_and_claim_session(
                    "P-first", "attempted", attempted_preparation,
                    lambda _history: {"accepted": True}, now=103,
                )
            self.assertEqual(
                replacement_active.exception.active_preparation_id,
                replacement_preparation,
            )

            released = state.force_release_participant_session(
                "P-first", "replacement", replacement_preparation,
            )
            self.assertEqual(released, {
                "participantId": "P-first",
                "released": True,
                "code": "abandoned_session_released",
                "releasedSessionId": "replacement",
                "releasedPreparationId": replacement_preparation,
                "activeSessionId": None,
                "activePreparationId": None,
            })
            self.assertFalse(
                state.force_release_participant_session(
                    "P-first", "replacement", replacement_preparation,
                )["released"],
            )
            with self.assertRaises(ActiveSessionLeaseError):
                state.audit_and_claim_session(
                    "P-second", "attempted", attempted_preparation,
                    lambda _history: {"accepted": True}, now=103,
                )

    def test_preflight_reports_exact_catalog_and_keeps_clean_candidate_seed(self):
        settings = {
            "split": "train",
            "signalMode": "mixed",
            "baseStimulusCount": 12,
            "glyphComposition": "automatic",
            "progression": "mixed",
            "feedbackEnabled": False,
            "seed": 4567,
        }
        effective, audit = plan_participant_session(
            settings, ROOT, "P1", set(),
        )
        self.assertEqual(effective["seed"], settings["seed"])
        self.assertEqual(audit["eligibleTransformations"], 217_271)
        self.assertEqual(
            audit["eligibleByGlyph"],
            {"1": 37, "2": 3_431, "3": 213_803},
        )
        self.assertEqual(audit["participantPreviouslySeen"], 0)
        self.assertEqual(audit["candidateStimuli"], 12)
        self.assertEqual(audit["historicalRepeatSlots"], 0)
        self.assertEqual(audit["repeatSlots"], 0)
        self.assertEqual(audit["repeatRate"], 0)
        self.assertEqual(audit["maximumRepeatSlots"], 1)
        self.assertEqual(audit["threshold"], HISTORICAL_REPEAT_THRESHOLD)
        self.assertTrue(audit["accepted"])
        self.assertEqual(audit["rerandomizations"], 0)
        self.assertEqual(audit["requestedSeed"], settings["seed"])
        self.assertEqual(audit["effectiveSeed"], settings["seed"])

    def test_preflight_rerandomizes_only_candidate_above_threshold(self):
        settings = {
            "split": "test",
            "signalMode": "paired",
            "baseStimulusCount": 12,
            "glyphComposition": "automatic",
            "progression": "mixed",
            "feedbackEnabled": False,
            "seed": 8127,
        }
        original = plan_session(settings, ROOT)
        original_signatures = {
            spec["transformationSignature"] for spec in original["base_specs"]
        }
        effective, audit = plan_participant_session(
            settings, ROOT, "repeat-participant", original_signatures,
        )
        self.assertGreaterEqual(audit["rerandomizations"], 1)
        self.assertNotEqual(effective["seed"], settings["seed"])
        self.assertEqual(audit["requestedSeed"], settings["seed"])
        self.assertEqual(audit["effectiveSeed"], effective["seed"])
        self.assertLessEqual(audit["repeatSlots"], 1)
        self.assertLessEqual(audit["repeatRate"], 0.10)
        self.assertTrue(audit["accepted"])

    def test_repeat_threshold_uses_base_slots_and_counts_candidate_duplicates(self):
        settings = {
            "split": "test",
            "signalMode": "paired",
            "baseStimulusCount": 96,
            "glyphComposition": "1",
            "progression": "mixed",
            "feedbackEnabled": False,
            "seed": 4,
        }
        with self.assertRaises(RepeatThresholdError) as captured:
            plan_participant_session(
                settings, ROOT, "P1", set(), max_attempts=1,
            )
        audit = captured.exception.audit
        self.assertEqual(audit["candidateStimuli"], 96)
        self.assertEqual(audit["candidateUniqueTransformations"], 19)
        self.assertEqual(audit["withinCandidateDuplicateSlots"], 77)
        self.assertEqual(audit["repeatSlots"], 77)
        self.assertEqual(audit["maximumRepeatSlots"], 9)
        self.assertFalse(audit["accepted"])

    def test_exact_candidate_reaudit_uses_current_history_and_ordered_digest(self):
        settings = {
            "split": "test",
            "signalMode": "visual",
            "baseStimulusCount": 12,
            "glyphComposition": "automatic",
            "progression": "mixed",
            "feedbackEnabled": False,
            "seed": 9042,
        }
        planned = plan_session(settings, ROOT)
        signatures = [
            spec["transformationSignature"] for spec in planned["base_specs"]
        ]
        clean = audit_participant_candidate(
            planned["settings"], ROOT, "P-current", set(), signatures, 9042, 0,
        )
        self.assertTrue(clean["accepted"])
        self.assertEqual(clean["candidateSignatureDigest"], signature_digest(signatures))
        self.assertNotEqual(
            signature_digest(signatures), signature_digest(list(reversed(signatures))),
        )

        newly_seen = set(signatures[:2])
        current = audit_participant_candidate(
            planned["settings"], ROOT, "P-current", newly_seen, signatures, 9042, 0,
        )
        self.assertEqual(current["historicalRepeatSlots"], 2)
        self.assertEqual(current["repeatSlots"], 2)
        self.assertFalse(current["accepted"])
        self.assertEqual(
            current["candidateSignatureDigest"], clean["candidateSignatureDigest"],
        )


class LocalParticipantHttpTests(unittest.TestCase):
    def test_interleaved_prepares_bind_their_own_requested_directories(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_root = Path(temp_name)
            repo_root = temp_root / "repo"
            data_root = repo_root / "test_data"
            session_root = temp_root / "sessions"
            first_results = temp_root / "results-a"
            second_results = temp_root / "results-b"
            grammar_root = repo_root / "advanced_ishihara"
            grammar_root.mkdir(parents=True)
            session_root.mkdir()
            first_results.mkdir()
            second_results.mkdir()
            (grammar_root / "grammar_snapshot.json").write_text(
                json.dumps(load_grammar(ROOT)),
            )

            render_barrier = threading.Barrier(2)

            def fake_prepare(settings, output_root, passed_repo_root):
                planned = plan_session(
                    settings, passed_repo_root,
                )
                render_barrier.wait(timeout=5)
                session_id = f"interleaved-{planned['settings']['seed']}"
                destination = Path(output_root) / session_id
                destination.mkdir(parents=True, exist_ok=True)
                manifest = {
                    "session_id": session_id,
                    "settings": planned["settings"],
                    "audio_generated": False,
                    "trials": [{} for _item in planned["base_specs"]],
                    "stimuli": [
                        {
                            "stimulus_id": f"stimulus_{index:03d}",
                            "transformation_signature": spec[
                                "transformationSignature"
                            ],
                        }
                        for index, spec in enumerate(
                            planned["base_specs"], start=1,
                        )
                    ],
                }
                manifest_path = destination / "manifest.json"
                manifest_path.write_text(json.dumps(manifest))
                return manifest_path.resolve(), manifest

            with patch(
                "shared.experiment_server.prepare_session",
                side_effect=fake_prepare,
            ):
                handler = make_handler(repo_root, data_root, session_root)
                handler.log_message = lambda *_args: None
                server = LocalThreadingServer(("127.0.0.1", 0), handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                try:
                    def prepare(seed, results_directory):
                        return self.post_json(base_url + "/api/prepare-session", {
                            "participantId": "P-interleaved",
                            "resultsDirectory": str(results_directory),
                            "split": "train",
                            "signalMode": "visual",
                            "baseStimulusCount": 4,
                            "glyphComposition": "automatic",
                            "progression": "mixed",
                            "feedbackEnabled": False,
                            "seed": seed,
                        })

                    with ThreadPoolExecutor(max_workers=2) as executor:
                        first_future = executor.submit(prepare, 101, first_results)
                        second_future = executor.submit(prepare, 202, second_results)
                        first = first_future.result(timeout=10)
                        second = second_future.result(timeout=10)

                    self.assertEqual(
                        first["saveDirectory"], str(first_results.resolve()),
                    )
                    self.assertEqual(
                        second["saveDirectory"], str(second_results.resolve()),
                    )
                    self.assertNotEqual(
                        first["preparationId"], second["preparationId"],
                    )
                    for label, prepared, directory in (
                        ("a", first, first_results),
                        ("b", second, second_results),
                    ):
                        saved = self.post_json(base_url + "/api/save-run", {
                            "participantId": "P-interleaved",
                            "sessionId": prepared["sessionId"],
                            "preparationId": prepared["preparationId"],
                            "candidateSignatureDigest": prepared[
                                "randomizationAudit"
                            ]["candidateSignatureDigest"],
                            "filename": f"{label}.csv",
                            "csv": f"binding\n{label}\n",
                        })
                        self.assertEqual(
                            saved["saveDirectory"], str(directory.resolve()),
                        )
                        self.assertTrue((directory / f"{label}.csv").is_file())
                    self.assertFalse((first_results / "b.csv").exists())
                    self.assertFalse((second_results / "a.csv").exists())

                    def revalidate(prepared):
                        return self.post_json(
                            base_url + "/api/revalidate-session",
                            {
                                "participantId": "P-interleaved",
                                "sessionId": prepared["sessionId"],
                                "preparationId": prepared["preparationId"],
                                "candidateSignatureDigest": prepared[
                                    "randomizationAudit"
                                ]["candidateSignatureDigest"],
                            },
                        )

                    first_revalidation = revalidate(first)
                    self.assertTrue(first_revalidation["sessionLease"]["acquired"])
                    with self.assertRaises(urllib.error.HTTPError) as competing:
                        revalidate(second)
                    self.assertEqual(competing.exception.code, 409)
                    competing_body = json.loads(competing.exception.read())
                    self.assertEqual(
                        competing_body["code"], "participant_session_active",
                    )
                    self.assertEqual(
                        competing_body["activeSessionId"], first["sessionId"],
                    )
                    self.assertEqual(
                        competing_body["activePreparationId"],
                        first["preparationId"],
                    )

                    with self.assertRaises(urllib.error.HTTPError) as unconfirmed:
                        self.post_json(
                            base_url + "/api/force-release-session",
                            {"participantId": "P-interleaved"},
                        )
                    self.assertEqual(unconfirmed.exception.code, 400)
                    self.assertEqual(
                        json.loads(unconfirmed.exception.read())["code"],
                        "confirmation_required",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as no_identity:
                        self.post_json(
                            base_url + "/api/force-release-session",
                            {
                                "participantId": "P-interleaved",
                                "confirmAbandonedSession": True,
                            },
                        )
                    self.assertEqual(no_identity.exception.code, 400)
                    self.assertEqual(
                        json.loads(no_identity.exception.read())["code"],
                        "invalid_request",
                    )
                    unrelated = self.post_json(
                        base_url + "/api/force-release-session",
                        {
                            "participantId": "P-unrelated",
                            "confirmAbandonedSession": True,
                            "expectedSessionId": competing_body[
                                "activeSessionId"
                            ],
                            "expectedPreparationId": competing_body[
                                "activePreparationId"
                            ],
                        },
                    )
                    self.assertFalse(unrelated["released"])
                    self.assertEqual(unrelated["code"], "no_active_session")
                    self.assertIsNone(unrelated["activeSessionId"])
                    self.assertIsNone(unrelated["activePreparationId"])
                    with self.assertRaises(urllib.error.HTTPError) as still_active:
                        revalidate(second)
                    self.assertEqual(
                        json.loads(still_active.exception.read())["code"],
                        "participant_session_active",
                    )

                    forced = self.post_json(
                        base_url + "/api/force-release-session",
                        {
                            "participantId": "P-interleaved",
                            "confirmAbandonedSession": True,
                            "expectedSessionId": competing_body[
                                "activeSessionId"
                            ],
                            "expectedPreparationId": competing_body[
                                "activePreparationId"
                            ],
                        },
                    )
                    self.assertTrue(forced["released"])
                    self.assertEqual(forced["code"], "abandoned_session_released")
                    self.assertEqual(
                        forced["releasedSessionId"], first["sessionId"],
                    )
                    self.assertEqual(
                        forced["releasedPreparationId"], first["preparationId"],
                    )
                    recovered = revalidate(second)
                    self.assertTrue(recovered["sessionLease"]["acquired"])
                    stale_force = self.post_json(
                        base_url + "/api/force-release-session",
                        {
                            "participantId": "P-interleaved",
                            "confirmAbandonedSession": True,
                            "expectedSessionId": competing_body[
                                "activeSessionId"
                            ],
                            "expectedPreparationId": competing_body[
                                "activePreparationId"
                            ],
                        },
                    )
                    self.assertFalse(stale_force["released"])
                    self.assertEqual(
                        stale_force["code"], "active_session_changed",
                    )
                    self.assertEqual(
                        stale_force["activeSessionId"], second["sessionId"],
                    )
                    self.assertEqual(
                        stale_force["activePreparationId"],
                        second["preparationId"],
                    )
                    still_owned = revalidate(second)
                    self.assertTrue(still_owned["sessionLease"]["renewed"])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

    def test_preferences_save_exposure_and_private_data_routes(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_root = Path(temp_name)
            repo_root = temp_root / "repo"
            data_root = repo_root / "test_data"
            session_root = temp_root / "sessions"
            result_root = temp_root / "results"
            later_result_root = temp_root / "later-results"
            mirror_result_root = temp_root / "mirror-results"
            repo_root.mkdir()
            session_root.mkdir()
            result_root.mkdir()
            later_result_root.mkdir()
            grammar_root = repo_root / "advanced_ishihara"
            grammar_root.mkdir()
            (grammar_root / "grammar_snapshot.json").write_text(
                json.dumps(load_grammar(ROOT)),
            )
            handler = make_handler(
                repo_root, data_root, session_root, mirror_result_root,
            )
            handler.log_message = lambda *_args: None
            server = LocalThreadingServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                initial = self.get_json(base_url + "/api/local-state")
                self.assertEqual(initial["participantId"], "")
                self.assertEqual(initial["participantUniqueSeen"], 0)
                registered = self.post_json(base_url + "/api/participants", {
                    "participantId": "P-new",
                })
                self.assertIn(
                    "P-new",
                    [item["participantId"] for item in registered["participants"]],
                )

                remembered = self.post_json(base_url + "/api/preferences", {
                    "participantId": "P-http",
                    "resultsDirectory": str(result_root),
                })
                self.assertEqual(remembered["participantId"], "P-http")
                self.assertEqual(
                    remembered["saveDirectory"], str(result_root.resolve()),
                )

                settings = {
                    "split": "train",
                    "signalMode": "visual",
                    "baseStimulusCount": 4,
                    "glyphComposition": "automatic",
                    "progression": "mixed",
                    "feedbackEnabled": False,
                    "seed": 731,
                }
                planned = plan_session(settings, repo_root)
                signatures = [
                    spec["transformationSignature"]
                    for spec in planned["base_specs"]
                ]
                digest = signature_digest(signatures)
                session_id = "advanced-http-test"
                manifest_dir = session_root / session_id
                manifest_dir.mkdir()
                (manifest_dir / "manifest.json").write_text(json.dumps({
                    "session_id": session_id,
                    "settings": planned["settings"],
                    "stimuli": [
                        {
                            "stimulus_id": f"stimulus_{index:03d}",
                            "transformation_signature": signature,
                        }
                        for index, signature in enumerate(signatures, start=1)
                    ],
                }))
                state = LocalParticipantState(data_root, data_root, repo_root)
                binding = state.create_prepared_session_binding(
                    "P-http", session_id, digest, 731, 731, 0, result_root,
                )
                preparation_id = binding["preparationId"]

                clean_revalidation = self.post_json(
                    base_url + "/api/revalidate-session",
                    {
                        "participantId": "P-http",
                        "sessionId": session_id,
                        "preparationId": preparation_id,
                        "candidateSignatureDigest": digest,
                    },
                )["randomizationAudit"]
                self.assertTrue(clean_revalidation["accepted"])
                self.assertEqual(
                    clean_revalidation["candidateSignatureDigest"], digest,
                )

                # A later preference change must not redirect this prepared run.
                self.post_json(base_url + "/api/preferences", {
                    "participantId": "P-http",
                    "resultsDirectory": str(later_result_root),
                })
                stimulus_id = "stimulus_001"
                signature = signatures[0]
                exposure_payload = {
                    "participantId": "P-http",
                    "sessionId": session_id,
                    "preparationId": preparation_id,
                    "stimulusId": stimulus_id,
                    "transformationSignature": signature,
                }
                self.assertTrue(self.post_json(
                    base_url + "/api/record-exposure", exposure_payload,
                )["recorded"])
                self.assertFalse(self.post_json(
                    base_url + "/api/record-exposure", exposure_payload,
                )["recorded"])
                self.assertEqual(
                    self.get_json(base_url + "/api/local-state")[
                        "participantUniqueSeen"
                    ],
                    1,
                )
                stale_revalidation = self.post_json(
                    base_url + "/api/revalidate-session",
                    {
                        "participantId": "P-http",
                        "sessionId": session_id,
                        "preparationId": preparation_id,
                        "candidateSignatureDigest": digest,
                    },
                )["randomizationAudit"]
                self.assertEqual(stale_revalidation["repeatSlots"], 1)
                self.assertFalse(stale_revalidation["accepted"])

                saved = self.post_json(base_url + "/api/save-run", {
                    "participantId": "P-http",
                    "sessionId": session_id,
                    "preparationId": preparation_id,
                    "candidateSignatureDigest": digest,
                    "filename": "pilot.csv",
                    "csv": "participant_id\nP-http\n",
                })
                self.assertEqual(saved["saveDirectory"], str(result_root.resolve()))
                self.assertEqual(
                    (result_root / "pilot.csv").read_text(),
                    "participant_id\nP-http\n",
                )
                self.assertEqual(
                    (mirror_result_root / "pilot.csv").read_text(),
                    "participant_id\nP-http\n",
                )
                self.assertEqual(
                    saved["paths"],
                    [
                        str((result_root / "pilot.csv").resolve()),
                        str((mirror_result_root / "pilot.csv").resolve()),
                    ],
                )
                second_save = self.post_json(base_url + "/api/save-run", {
                    "participantId": "P-http",
                    "sessionId": session_id,
                    "preparationId": preparation_id,
                    "candidateSignatureDigest": digest,
                    "filename": "pilot.csv",
                    "csv": "second\nrun\n",
                    # Per-save paths are deliberately ignored; only the
                    # validated remembered preference controls the target.
                    "resultsDirectory": str(temp_root / "not-selected"),
                })
                self.assertEqual(
                    Path(second_save["path"]).name, "pilot_2.csv",
                )
                self.assertEqual(
                    (result_root / "pilot_2.csv").read_text(), "second\nrun\n",
                )
                self.assertEqual(
                    (mirror_result_root / "pilot_2.csv").read_text(),
                    "second\nrun\n",
                )
                self.assertFalse((later_result_root / "pilot.csv").exists())

                released = self.post_json(base_url + "/api/release-session", {
                    "participantId": "P-http",
                    "sessionId": session_id,
                    "preparationId": preparation_id,
                })
                self.assertTrue(released["released"])
                saved_after_release = self.post_json(base_url + "/api/save-run", {
                    "participantId": "P-http",
                    "sessionId": session_id,
                    "preparationId": preparation_id,
                    "candidateSignatureDigest": digest,
                    "filename": "after-release.csv",
                    "csv": "save\nstill-works\n",
                })
                self.assertEqual(
                    saved_after_release["saveDirectory"], str(result_root.resolve()),
                )
                with self.assertRaises(urllib.error.HTTPError) as inactive:
                    self.post_json(base_url + "/api/record-exposure", {
                        **exposure_payload,
                        "stimulusId": "stimulus_002",
                        "transformationSignature": signatures[1],
                    })
                self.assertEqual(inactive.exception.code, 409)

                with self.assertRaises(urllib.error.HTTPError) as unbound:
                    self.post_json(base_url + "/api/save-run", {
                        "participantId": "other-participant",
                        "sessionId": session_id,
                        "preparationId": preparation_id,
                        "candidateSignatureDigest": digest,
                        "filename": "forbidden.csv",
                        "csv": "no\n",
                    })
                self.assertEqual(unbound.exception.code, 400)

                hostile_request = urllib.request.Request(
                    base_url + "/api/preferences",
                    data=json.dumps({"participantId": "attacker"}).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "https://example.com",
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as forbidden:
                    urllib.request.urlopen(hostile_request, timeout=5)
                self.assertEqual(forbidden.exception.code, 403)

                for method in ("GET", "HEAD"):
                    request = urllib.request.Request(
                        base_url + "/test_data/participant_history.sqlite3",
                        method=method,
                    )
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    @staticmethod
    def get_json(url):
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read())

    @staticmethod
    def post_json(url, payload):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())


if __name__ == "__main__":
    unittest.main()
