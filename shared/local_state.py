"""Local participant preferences, exposure history, and preflight planning.

The database belongs in the application's private data directory (``test_data``
in a source checkout, Application Support in the packaged app).  It is never a
session asset and must not be served by the HTTP server.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from advanced_ishihara.generate_session import (
    derive_seed,
    eligible_transformation_counts,
    load_grammar,
    normalize_settings,
    plan_session,
)

HISTORICAL_REPEAT_THRESHOLD = 0.10
MAX_RANDOMIZATION_ATTEMPTS = 128
DATABASE_FILENAME = "participant_history.sqlite3"
SESSION_LEASE_INACTIVITY_SECONDS = 60 * 60


class RepeatThresholdError(ValueError):
    """Raised when no candidate satisfies the historical-repeat threshold."""

    def __init__(self, audit: dict):
        self.audit = audit
        super().__init__(
            "Could not produce a candidate at or below the 10% repeat "
            "threshold. Try fewer stimuli, another glyph composition, "
            "or a different source set."
        )


class ActiveSessionLeaseError(ValueError):
    """Raised when another preparation owns a participant's active lease."""

    code = "participant_session_active"

    def __init__(
        self,
        active_session_id: str | None = None,
        active_preparation_id: str | None = None,
    ):
        self.active_session_id = active_session_id
        self.active_preparation_id = active_preparation_id
        super().__init__(
            "This participant already has an active session. Complete or close "
            "that block, or wait for its 60-minute inactivity lease to expire."
        )


class LocalParticipantState:
    """Small, thread-safe-by-connection SQLite store for local-only state."""

    def __init__(
        self,
        state_directory: Path,
        default_save_directory: Path,
        repo_root: Path,
    ) -> None:
        self.state_directory = state_directory.expanduser().resolve()
        self.default_save_directory = default_save_directory.expanduser().resolve()
        self.repo_root = repo_root.resolve()
        self.state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_directory.chmod(0o700)
        self.default_save_directory.mkdir(parents=True, exist_ok=True)
        self.database_path = self.state_directory / DATABASE_FILENAME
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS participants (
                    participant_id TEXT PRIMARY KEY,
                    registered_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS exposures (
                    participant_id TEXT NOT NULL,
                    transformation_signature TEXT NOT NULL,
                    first_session_id TEXT NOT NULL,
                    first_stimulus_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    PRIMARY KEY (participant_id, transformation_signature)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS exposures_by_participant
                ON exposures (participant_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prepared_session_bindings (
                    preparation_id TEXT PRIMARY KEY,
                    participant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    save_directory TEXT NOT NULL,
                    candidate_signature_digest TEXT NOT NULL,
                    requested_seed INTEGER NOT NULL,
                    effective_seed INTEGER NOT NULL,
                    rerandomizations INTEGER NOT NULL,
                    prepared_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS prepared_bindings_by_session
                ON prepared_session_bindings (participant_id, session_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS participant_session_leases (
                    participant_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    preparation_id TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    last_activity_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            now = utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO participants (
                    participant_id, registered_at, last_used_at
                )
                SELECT DISTINCT participant_id, ?, ? FROM exposures
                """,
                (now, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO participants (
                    participant_id, registered_at, last_used_at
                )
                SELECT DISTINCT participant_id, ?, ?
                FROM prepared_session_bindings
                """,
                (now, now),
            )
            remembered = connection.execute(
                "SELECT value FROM preferences WHERE key = 'last_participant_id'"
            ).fetchone()
            if remembered and remembered[0].strip():
                connection.execute(
                    """
                    INSERT OR IGNORE INTO participants (
                        participant_id, registered_at, last_used_at
                    ) VALUES (?, ?, ?)
                    """,
                    (remembered[0].strip(), now, now),
                )
        self.database_path.chmod(0o600)

    def preferences(self) -> dict:
        with self._connect() as connection:
            values = dict(connection.execute(
                "SELECT key, value FROM preferences"
            ).fetchall())
        return {
            "participantId": values.get("last_participant_id", ""),
            "saveDirectory": values.get(
                "save_directory", str(self.default_save_directory),
            ),
            "defaultSaveDirectory": str(self.default_save_directory),
            "historyPath": str(self.database_path),
        }

    def update_preferences(
        self,
        participant_id: object | None = None,
        save_directory: object | None = None,
    ) -> dict:
        updates: dict[str, str] = {}
        if participant_id is not None:
            participant = normalize_participant_id(participant_id)
            self.register_participant(participant)
            updates["last_participant_id"] = participant
        if save_directory is not None:
            updates["save_directory"] = str(self.validate_save_directory(save_directory))
        if updates:
            now = utc_now()
            with self._connect() as connection:
                connection.executemany(
                    """
                    INSERT INTO preferences (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    [(key, value, now) for key, value in updates.items()],
                )
        return self.preferences()

    def register_participant(self, participant_id: object) -> dict:
        participant = normalize_participant_id(participant_id)
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO participants (
                    participant_id, registered_at, last_used_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(participant_id) DO UPDATE SET
                    last_used_at = excluded.last_used_at
                """,
                (participant, now, now),
            )
        return next(
            item for item in self.participants()
            if item["participantId"] == participant
        )

    def participants(self) -> list[dict]:
        timestamp = time.time()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.participant_id, p.registered_at, p.last_used_at,
                       COUNT(e.transformation_signature),
                       CASE WHEN l.expires_at > ? THEN 1 ELSE 0 END
                FROM participants AS p
                LEFT JOIN exposures AS e
                  ON e.participant_id = p.participant_id
                LEFT JOIN participant_session_leases AS l
                  ON l.participant_id = p.participant_id
                GROUP BY p.participant_id, p.registered_at, p.last_used_at,
                         l.expires_at
                ORDER BY p.last_used_at DESC, p.participant_id COLLATE NOCASE
                """,
                (timestamp,),
            ).fetchall()
        return [
            {
                "participantId": row[0],
                "registeredAt": row[1],
                "lastUsedAt": row[2],
                "participantUniqueSeen": int(row[3]),
                "activeSession": bool(row[4]),
            }
            for row in rows
        ]

    def validate_save_directory(self, value: object) -> Path:
        raw = str(value).strip()
        if not raw:
            raise ValueError("Results directory is required")
        if len(raw) > 4096 or "\x00" in raw:
            raise ValueError("Results directory is invalid")
        expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not expanded.is_absolute():
            raise ValueError("Results directory must be an absolute path")
        directory = expanded.resolve()

        # Source checkouts may only write participant data under the already
        # ignored default data tree. External absolute paths remain available.
        try:
            directory.relative_to(self.repo_root)
        except ValueError:
            pass
        else:
            try:
                directory.relative_to(self.default_save_directory)
            except ValueError as error:
                raise ValueError(
                    "Results inside the source repository must stay under "
                    f"{self.default_save_directory}"
                ) from error

        if not directory.exists():
            raise ValueError("Results directory does not exist")
        if not directory.is_dir():
            raise ValueError("Results directory is not a directory")
        if not os.access(directory, os.W_OK | os.X_OK):
            raise ValueError("Results directory is not writable")
        return directory

    def current_save_directory(self) -> Path:
        remembered = self.preferences()["saveDirectory"]
        return self.validate_save_directory(remembered)

    def create_prepared_session_binding(
        self,
        participant_id: object,
        session_id: object,
        candidate_signature_digest: object,
        requested_seed: object,
        effective_seed: object,
        rerandomizations: object,
        save_directory: object | None = None,
    ) -> dict:
        participant = normalize_participant_id(participant_id)
        session = normalize_ledger_text(session_id, "sessionId", 240)
        digest = normalize_signature_digest(candidate_signature_digest)
        requested = normalize_uint32(requested_seed, "requestedSeed")
        effective = normalize_uint32(effective_seed, "effectiveSeed")
        rerolls = normalize_nonnegative_integer(
            rerandomizations, "rerandomizations",
        )
        directory = (
            self.current_save_directory()
            if save_directory is None
            else self.validate_save_directory(save_directory)
        )
        preparation_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO prepared_session_bindings (
                    preparation_id, participant_id, session_id, save_directory,
                    candidate_signature_digest, requested_seed, effective_seed,
                    rerandomizations, prepared_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preparation_id, participant, session, str(directory), digest,
                    requested, effective, rerolls, utc_now(),
                ),
            )
        return {
            "preparationId": preparation_id,
            "participantId": participant,
            "sessionId": session,
            "saveDirectory": str(directory),
            "candidateSignatureDigest": digest,
            "requestedSeed": requested,
            "effectiveSeed": effective,
            "rerandomizations": rerolls,
        }

    def prepared_session_binding(
        self,
        participant_id: object,
        session_id: object,
        preparation_id: object,
    ) -> dict:
        participant = normalize_participant_id(participant_id)
        session = normalize_ledger_text(session_id, "sessionId", 240)
        preparation = normalize_preparation_id(preparation_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT save_directory, candidate_signature_digest,
                       requested_seed, effective_seed, rerandomizations
                FROM prepared_session_bindings
                WHERE preparation_id = ? AND participant_id = ? AND session_id = ?
                """,
                (preparation, participant, session),
            ).fetchone()
        if row is None or any(value is None for value in row):
            raise ValueError("No prepared binding exists for this participant and session")
        return {
            "participantId": participant,
            "sessionId": session,
            "preparationId": preparation,
            # Binding identity and participant history remain usable if an
            # external results volume is temporarily unavailable. Filesystem
            # validation belongs only to the actual CSV save operation.
            "saveDirectory": str(row[0]),
            "candidateSignatureDigest": normalize_signature_digest(row[1]),
            "requestedSeed": normalize_uint32(row[2], "requestedSeed"),
            "effectiveSeed": normalize_uint32(row[3], "effectiveSeed"),
            "rerandomizations": normalize_nonnegative_integer(
                row[4], "rerandomizations",
            ),
        }

    def session_save_directory(
        self,
        participant_id: object,
        session_id: object,
        preparation_id: object,
        candidate_signature_digest: object,
    ) -> Path:
        binding = self.prepared_session_binding(
            participant_id, session_id, preparation_id,
        )
        digest = normalize_signature_digest(candidate_signature_digest)
        if digest != binding["candidateSignatureDigest"]:
            raise ValueError("Candidate signature digest does not match prepared session")
        return self.validate_save_directory(binding["saveDirectory"])

    def audit_and_claim_session(
        self,
        participant_id: object,
        session_id: object,
        preparation_id: object,
        audit_builder,
        now: float | None = None,
    ) -> tuple[dict, dict | None]:
        """Atomically audit current history and claim this participant's lease."""
        participant = normalize_participant_id(participant_id)
        session = normalize_ledger_text(session_id, "sessionId", 240)
        preparation = normalize_preparation_id(preparation_id)
        timestamp = time.time() if now is None else float(now)
        expires_at = timestamp + SESSION_LEASE_INACTIVITY_SECONDS
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            history = {
                row[0] for row in connection.execute(
                    """
                    SELECT transformation_signature FROM exposures
                    WHERE participant_id = ?
                    """,
                    (participant,),
                ).fetchall()
            }
            audit = audit_builder(history)
            if not audit.get("accepted"):
                connection.commit()
                return audit, None

            existing = connection.execute(
                """
                SELECT session_id, preparation_id, acquired_at, expires_at
                FROM participant_session_leases
                WHERE participant_id = ?
                """,
                (participant,),
            ).fetchone()
            same_owner = bool(
                existing
                and existing[0] == session
                and existing[1] == preparation
            )
            active = bool(existing and float(existing[3]) > timestamp)
            if active and not same_owner:
                raise ActiveSessionLeaseError(existing[0], existing[1])

            acquired_at = float(existing[2]) if active and same_owner else timestamp
            connection.execute(
                """
                INSERT INTO participant_session_leases (
                    participant_id, session_id, preparation_id, acquired_at,
                    last_activity_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(participant_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    preparation_id = excluded.preparation_id,
                    acquired_at = excluded.acquired_at,
                    last_activity_at = excluded.last_activity_at,
                    expires_at = excluded.expires_at
                """,
                (
                    participant, session, preparation, acquired_at,
                    timestamp, expires_at,
                ),
            )
            connection.commit()
            return audit, {
                "participantId": participant,
                "sessionId": session,
                "preparationId": preparation,
                "acquired": not (active and same_owner),
                "renewed": active and same_owner,
                "expiresAt": timestamp_as_utc(expires_at),
                "inactivityTtlSeconds": SESSION_LEASE_INACTIVITY_SECONDS,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def release_session(
        self,
        participant_id: object,
        session_id: object,
        preparation_id: object,
    ) -> bool:
        participant = normalize_participant_id(participant_id)
        session = normalize_ledger_text(session_id, "sessionId", 240)
        preparation = normalize_preparation_id(preparation_id)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM participant_session_leases
                WHERE participant_id = ? AND session_id = ? AND preparation_id = ?
                """,
                (participant, session, preparation),
            )
        return cursor.rowcount == 1

    def force_release_participant_session(
        self,
        participant_id: object,
        expected_session_id: object,
        expected_preparation_id: object,
    ) -> dict:
        """Clear an abandoned lease only if its observed identity is unchanged."""
        participant = normalize_participant_id(participant_id)
        expected_session = normalize_ledger_text(
            expected_session_id, "expectedSessionId", 240,
        )
        expected_preparation = normalize_preparation_id(expected_preparation_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT session_id, preparation_id
                FROM participant_session_leases
                WHERE participant_id = ?
                """,
                (participant,),
            ).fetchone()
            if row is None:
                connection.commit()
                return {
                    "participantId": participant,
                    "released": False,
                    "code": "no_active_session",
                    "releasedSessionId": None,
                    "releasedPreparationId": None,
                    "activeSessionId": None,
                    "activePreparationId": None,
                }
            if row[0] != expected_session or row[1] != expected_preparation:
                connection.commit()
                return {
                    "participantId": participant,
                    "released": False,
                    "code": "active_session_changed",
                    "releasedSessionId": None,
                    "releasedPreparationId": None,
                    "activeSessionId": row[0],
                    "activePreparationId": row[1],
                }
            cursor = connection.execute(
                """
                DELETE FROM participant_session_leases
                WHERE participant_id = ? AND session_id = ? AND preparation_id = ?
                """,
                (participant, expected_session, expected_preparation),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Active session changed during force release")
            connection.commit()
            return {
                "participantId": participant,
                "released": True,
                "code": "abandoned_session_released",
                "releasedSessionId": row[0],
                "releasedPreparationId": row[1],
                "activeSessionId": None,
                "activePreparationId": None,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def history_signatures(self, participant_id: object) -> set[str]:
        participant = normalize_participant_id(participant_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT transformation_signature
                FROM exposures
                WHERE participant_id = ?
                """,
                (participant,),
            ).fetchall()
        return {row[0] for row in rows}

    def record_exposure(
        self,
        participant_id: object,
        session_id: object,
        stimulus_id: object,
        transformation_signature: object,
    ) -> dict:
        participant = normalize_participant_id(participant_id)
        session = normalize_ledger_text(session_id, "sessionId", 240)
        stimulus = normalize_ledger_text(stimulus_id, "stimulusId", 240)
        signature = normalize_ledger_text(
            transformation_signature, "transformationSignature", 4096,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO exposures (
                    participant_id,
                    transformation_signature,
                    first_session_id,
                    first_stimulus_id,
                    first_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (participant, signature, session, stimulus, utc_now()),
            )
            unique_seen = connection.execute(
                """
                SELECT COUNT(*) FROM exposures WHERE participant_id = ?
                """,
                (participant,),
            ).fetchone()[0]
        return {
            "recorded": cursor.rowcount == 1,
            "participantUniqueSeen": unique_seen,
        }

    def record_active_exposure(
        self,
        participant_id: object,
        session_id: object,
        preparation_id: object,
        stimulus_id: object,
        transformation_signature: object,
        now: float | None = None,
    ) -> dict:
        """Record one onset and renew its matching participant lease atomically."""
        participant = normalize_participant_id(participant_id)
        session = normalize_ledger_text(session_id, "sessionId", 240)
        preparation = normalize_preparation_id(preparation_id)
        stimulus = normalize_ledger_text(stimulus_id, "stimulusId", 240)
        signature = normalize_ledger_text(
            transformation_signature, "transformationSignature", 4096,
        )
        timestamp = time.time() if now is None else float(now)
        expires_at = timestamp + SESSION_LEASE_INACTIVITY_SECONDS
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                """
                SELECT session_id, preparation_id, expires_at
                FROM participant_session_leases
                WHERE participant_id = ?
                """,
                (participant,),
            ).fetchone()
            if (
                lease is None
                or lease[0] != session
                or lease[1] != preparation
                or float(lease[2]) <= timestamp
            ):
                if lease is not None and float(lease[2]) > timestamp:
                    raise ActiveSessionLeaseError(lease[0], lease[1])
                raise ActiveSessionLeaseError()
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO exposures (
                    participant_id, transformation_signature,
                    first_session_id, first_stimulus_id, first_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (participant, signature, session, stimulus, utc_now()),
            )
            connection.execute(
                """
                UPDATE participant_session_leases
                SET last_activity_at = ?, expires_at = ?
                WHERE participant_id = ? AND session_id = ? AND preparation_id = ?
                """,
                (timestamp, expires_at, participant, session, preparation),
            )
            unique_seen = connection.execute(
                "SELECT COUNT(*) FROM exposures WHERE participant_id = ?",
                (participant,),
            ).fetchone()[0]
            connection.commit()
            return {
                "recorded": cursor.rowcount == 1,
                "participantUniqueSeen": unique_seen,
                "leaseExpiresAt": timestamp_as_utc(expires_at),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def normalize_participant_id(value: object) -> str:
    participant = str(value).strip()
    if not participant:
        raise ValueError("Participant ID is required")
    if len(participant) > 200:
        raise ValueError("Participant ID must be at most 200 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in participant):
        raise ValueError("Participant ID cannot contain control characters")
    return participant


def normalize_ledger_text(value: object, field_name: str, limit: int) -> str:
    text = str(value).strip()
    if not text or len(text) > limit or "\x00" in text:
        raise ValueError(f"{field_name} is invalid")
    return text


def normalize_signature_digest(value: object) -> str:
    digest = str(value).strip().lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValueError("candidateSignatureDigest must be a SHA-256 hex digest")
    return digest


def normalize_preparation_id(value: object) -> str:
    try:
        preparation_id = str(uuid.UUID(str(value).strip()))
    except (AttributeError, ValueError) as error:
        raise ValueError("preparationId must be a UUID") from error
    return preparation_id


def normalize_nonnegative_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer")
    try:
        integer = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a non-negative integer") from error
    if integer < 0 or str(integer) != str(value).strip():
        raise ValueError(f"{field_name} must be a non-negative integer")
    return integer


def normalize_uint32(value: object, field_name: str) -> int:
    integer = normalize_nonnegative_integer(value, field_name)
    if integer > 0xFFFFFFFF:
        raise ValueError(f"{field_name} must be an unsigned 32-bit integer")
    return integer


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_as_utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def candidate_signatures(plan: dict) -> list[str]:
    """Return the canonical lightweight plan's base signatures."""
    return [
        spec["transformationSignature"] for spec in plan["base_specs"]
    ]


def signature_digest(signatures: list[str]) -> str:
    serialized = json.dumps(
        signatures, ensure_ascii=False, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def _candidate_audit(
    signatures: list[str],
    participant: str,
    history_signatures: set[str],
    eligible_total: int,
    eligible_by_glyph: dict[str, int],
    eligible_history_count: int,
    threshold: float,
    requested_seed: int,
    effective_seed: int,
    rerandomizations: int,
) -> dict:
    unique_signatures = set(signatures)
    historical_repeat_slots = sum(
        signature in history_signatures for signature in signatures
    )
    seen_in_candidate: set[str] = set()
    within_candidate_duplicate_slots = 0
    repeat_slots = 0
    for signature in signatures:
        repeated_in_candidate = signature in seen_in_candidate
        if repeated_in_candidate:
            within_candidate_duplicate_slots += 1
        if signature in history_signatures or repeated_in_candidate:
            repeat_slots += 1
        seen_in_candidate.add(signature)
    candidate_unique = len(unique_signatures)
    candidate_count = len(signatures)
    historical_repeat_rate = (
        historical_repeat_slots / candidate_count if candidate_count else 0.0
    )
    repeat_rate = repeat_slots / candidate_count if candidate_count else 0.0
    if threshold == HISTORICAL_REPEAT_THRESHOLD:
        maximum_repeats = candidate_count // 10
        accepted = 10 * repeat_slots <= candidate_count
    else:
        maximum_repeats = math.floor(candidate_count * threshold + 1e-12)
        accepted = repeat_slots <= maximum_repeats
    return {
        "participantId": participant,
        "eligibleTransformations": eligible_total,
        "eligibleByGlyph": eligible_by_glyph,
        "participantPreviouslySeen": eligible_history_count,
        "participantPreviouslySeenAll": len(history_signatures),
        "candidateStimuli": candidate_count,
        "candidateUniqueTransformations": candidate_unique,
        "candidateSignatureDigest": signature_digest(signatures),
        "historicalRepeats": historical_repeat_slots,
        "historicalRepeatSlots": historical_repeat_slots,
        "historicalRepeatRate": round(historical_repeat_rate, 10),
        "withinCandidateDuplicateSlots": within_candidate_duplicate_slots,
        "repeatSlots": repeat_slots,
        "repeatRate": round(repeat_rate, 10),
        "maximumHistoricalRepeats": maximum_repeats,
        "maximumRepeatSlots": maximum_repeats,
        "threshold": threshold,
        "accepted": accepted,
        "rerandomizations": rerandomizations,
        "requestedSeed": requested_seed,
        "effectiveSeed": effective_seed,
    }


def audit_participant_candidate(
    settings: dict,
    repo_root: Path,
    participant_id: object,
    history_signatures: set[str],
    signatures: list[str],
    requested_seed: object,
    rerandomizations: object,
    threshold: float = HISTORICAL_REPEAT_THRESHOLD,
    grammar: dict | None = None,
) -> dict:
    """Audit one exact ordered candidate against current participant history."""
    if not 0 <= threshold <= 1:
        raise ValueError("repeat threshold must be between zero and one")
    participant = normalize_participant_id(participant_id)
    requested = normalize_uint32(requested_seed, "requestedSeed")
    rerolls = normalize_nonnegative_integer(
        rerandomizations, "rerandomizations",
    )
    normalized = normalize_settings(settings)
    effective_seed = normalized["seed"]
    if len(signatures) != normalized["baseStimulusCount"]:
        raise ValueError("Candidate signatures do not match base stimulus count")
    exact_signatures = []
    for signature in signatures:
        if not isinstance(signature, str):
            raise ValueError("Candidate transformation signature is invalid")
        exact = normalize_ledger_text(
            signature, "transformationSignature", 4096,
        )
        if exact != signature:
            raise ValueError("Candidate transformation signature is not canonical")
        exact_signatures.append(exact)

    grammar = grammar or load_grammar(repo_root)
    counts = eligible_transformation_counts(grammar, normalized["split"])
    enabled_lengths = (
        (1, 2, 3)
        if normalized["glyphComposition"] == "automatic"
        else (int(normalized["glyphComposition"]),)
    )
    if any(
        not signature_is_eligible(signature, grammar, normalized)
        for signature in exact_signatures
    ):
        raise ValueError("Candidate contains an ineligible transformation signature")
    eligible_by_glyph = {
        str(length): counts[length] for length in enabled_lengths
    }
    eligible_history_count = sum(
        signature_is_eligible(signature, grammar, normalized)
        for signature in history_signatures
    )
    return _candidate_audit(
        exact_signatures,
        participant,
        history_signatures,
        sum(eligible_by_glyph.values()),
        eligible_by_glyph,
        eligible_history_count,
        threshold,
        requested,
        effective_seed,
        rerolls,
    )


def plan_participant_session(
    settings: dict,
    repo_root: Path,
    participant_id: object,
    history_signatures: set[str],
    threshold: float = HISTORICAL_REPEAT_THRESHOLD,
    max_attempts: int = MAX_RANDOMIZATION_ATTEMPTS,
) -> tuple[dict, dict]:
    """Return accepted effective settings and a participant-wise audit.

    Attempt zero is the user's candidate unchanged.  Only a candidate above the
    threshold is rerandomized. Repeated-pair presentations are not inflated:
    this function evaluates the base stimulus slots, not both presentations.
    """
    if not 0 <= threshold <= 1:
        raise ValueError("repeat threshold must be between zero and one")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    participant = normalize_participant_id(participant_id)
    grammar = load_grammar(repo_root)
    requested_plan = plan_session(settings, repo_root, grammar=grammar)
    requested = requested_plan["settings"]
    requested_seed = requested["seed"]
    enabled_lengths = {
        length
        for length, quota in requested_plan["glyph_count_quotas"].items()
        if quota
    }
    eligible_by_glyph = {
        str(length): count
        for length, count in requested_plan["eligible_by_glyph_count"].items()
        if length in enabled_lengths
    }
    eligible_history = {
        signature for signature in history_signatures
        if signature_is_eligible(signature, grammar, requested)
    }
    attempted_seeds: set[int] = set()
    best_audit = None

    for attempt in range(max_attempts):
        effective_seed = rerandomized_seed(requested_seed, attempt)
        while effective_seed in attempted_seeds:
            effective_seed = (effective_seed + 1) & 0xFFFFFFFF
        attempted_seeds.add(effective_seed)
        effective = {**requested, "seed": effective_seed}
        candidate_plan = (
            requested_plan if attempt == 0
            else plan_session(effective, repo_root, grammar=grammar)
        )
        effective = candidate_plan["settings"]
        signatures = candidate_signatures(candidate_plan)
        audit = _candidate_audit(
            signatures,
            participant,
            history_signatures,
            requested_plan["eligible_transformation_count"],
            eligible_by_glyph,
            len(eligible_history),
            threshold,
            requested_seed,
            effective_seed,
            attempt,
        )
        if best_audit is None or (
            audit["repeatRate"], audit["repeatSlots"]
        ) < (
            best_audit["repeatRate"],
            best_audit["repeatSlots"],
        ):
            best_audit = audit
        if audit["accepted"]:
            return effective, audit

    assert best_audit is not None
    best_audit = {
        **best_audit,
        "accepted": False,
        "rerandomizations": max_attempts - 1,
        "attempts": max_attempts,
    }
    raise RepeatThresholdError(best_audit)


def rerandomized_seed(requested_seed: int, attempt: int) -> int:
    if attempt == 0:
        return requested_seed
    return derive_seed(
        requested_seed, f"participant-history-rerandomize-v1:{attempt}",
    ) & 0xFFFFFFFF


def signature_is_eligible(
    signature: str,
    grammar: dict,
    normalized_settings: dict,
) -> bool:
    """Test eligibility without enumerating the potentially large catalog."""
    parts = signature.split("|") if signature else []
    glyph_composition = normalized_settings["glyphComposition"]
    allowed_lengths = (
        {1, 2, 3} if glyph_composition == "automatic"
        else {int(glyph_composition)}
    )
    if len(parts) not in allowed_lengths:
        return False
    family_by_source = {
        family["sourceId"]: family
        for family in grammar["sourceFamilies"]
        if family["split"] == normalized_settings["split"]
    }
    mappings = []
    for part in parts:
        if "--" not in part:
            return False
        source, target = part.split("--", 1)
        family = family_by_source.get(source)
        if family is None:
            return False
        if target not in {source, *family["changedTargetIds"]}:
            return False
        mappings.append((family, source, target))
    if not any(source != target for _family, source, target in mappings):
        return False
    if len(parts) == 1:
        family, source, target = mappings[0]
        return family["familySize"] >= 4 and source != target
    return True


def audit_as_json(audit: dict) -> str:
    """Stable representation useful for logs and tests."""
    return json.dumps(audit, sort_keys=True, separators=(",", ":"))
