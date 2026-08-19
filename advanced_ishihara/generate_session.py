#!/usr/bin/env python3
"""Generate and cache only the assets required by one advanced session."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.plate import (
    AUDIO_HEIGHT,
    AUDIO_WIDTH,
    GEOMETRY_SEGMENTS,
    PLATE_HEIGHT,
    PLATE_WIDTH,
    render_trial_images,
    segment_closure_relations,
)
from shared.soundscape import (
    SAMPLE_RATE_HZ,
    SAMPLES_PER_COLUMN,
    SWEEP_DURATION_S,
    TARGET_RMS_INT16,
    default_raspivoice_bin,
    generate_soundscape,
    normalize_wav_rms,
    validate_wav,
    wav_rms_int16,
)

SCHEMA_VERSION = 3
SWEEP_REPETITIONS = 3
INTER_SWEEP_INTERVAL_MS = 250
MASK_DURATION_MS = 220


def load_grammar(repo_root: Path = REPO_ROOT) -> dict:
    snapshot = repo_root / "advanced_ishihara" / "grammar_snapshot.json"
    if snapshot.is_file():
        grammar = json.loads(snapshot.read_text())
    else:
        node_binary = os.environ.get("NODE_BIN") or shutil.which("node")
        if not node_binary:
            raise RuntimeError(
                "Node.js is required to read the canonical grammar. Set NODE_BIN."
            )
        completed = subprocess.run(
            [
                node_binary,
                str(repo_root / "tools" / "export_advanced_catalog.mjs"),
                "--format=grammar",
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        grammar = json.loads(completed.stdout)
    validate_geometry_implementation(grammar)
    return grammar


def validate_geometry_implementation(grammar: dict) -> None:
    grammar_ids = {item["id"] for item in grammar["geometries"]}
    if grammar_ids != set(GEOMETRY_SEGMENTS):
        raise RuntimeError("drawn geometry IDs do not match the canonical grammar")
    expected_relations = {
        (item["sourceId"], item["targetId"])
        for item in grammar["mappings"]
        if item["changed"]
    }
    actual_relations = segment_closure_relations()
    if expected_relations != actual_relations:
        missing = sorted(expected_relations - actual_relations)
        extra = sorted(actual_relations - expected_relations)
        raise RuntimeError(
            f"drawn geometry containment differs from grammar; missing={missing}, extra={extra}"
        )


def normalize_settings(settings: dict) -> dict:
    split = settings.get("split", "train")
    mode = settings.get("mode", "visual-only")
    if split not in {"train", "test"}:
        raise ValueError("split must be train or test")
    if mode not in {"mixed", "visual-only"}:
        raise ValueError("mode must be mixed or visual-only")
    trial_count = int(settings.get("trialCount", 12))
    if not 4 <= trial_count <= 96:
        raise ValueError("trialCount must be between 4 and 96")
    if mode == "mixed" and trial_count % 2:
        raise ValueError("mixed sessions require an even trialCount")
    seed = int(settings.get("seed", 1729)) & 0xFFFFFFFF
    return {
        "split": split,
        "mode": mode,
        "trialCount": trial_count,
        "seed": seed,
        "schemaVersion": SCHEMA_VERSION,
    }


def prepare_session(
    settings: dict,
    output_root: Path,
    repo_root: Path = REPO_ROOT,
) -> tuple[Path, dict]:
    normalized = normalize_settings(settings)
    session_key = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    session_id = f"advanced-{session_key}"
    destination = output_root / session_id
    manifest_path = destination / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        if manifest_is_complete(manifest, destination):
            return manifest_path, manifest

    output_root.mkdir(parents=True, exist_ok=True)
    grammar = load_grammar(repo_root)
    rng = random.Random(normalized["seed"])
    families = [
        family
        for family in grammar["sourceFamilies"]
        if family["split"] == normalized["split"]
    ]
    base_count = (
        normalized["trialCount"] // 2
        if normalized["mode"] == "mixed"
        else normalized["trialCount"]
    )
    base_specs = select_base_trials(families, base_count, rng)

    with tempfile.TemporaryDirectory(prefix="advanced-build-", dir=output_root) as temp_name:
        build_root = Path(temp_name)
        stimuli = []
        raspivoice_bin = default_raspivoice_bin(repo_root)
        for index, spec in enumerate(base_specs, start=1):
            stem = f"stimulus_{index:03d}"
            assets = render_trial_images(
                spec["sourceIds"],
                spec["targetIds"],
                spec["choiceTargets"],
                build_root,
                stem,
                normalized["seed"] + index * 104729,
            )
            target_choice = next(
                item for item in assets["choices"]
                if item["target_ids"] == spec["targetIds"]
            )
            decoy_choice = next(
                item for item in assets["choices"]
                if item["target_ids"] == spec["sourceIds"]
            )

            probe_wav = None
            background_wav = None
            matched_rms = None
            if normalized["mode"] == "mixed":
                audio_dir = build_root / "audio"
                audio_dir.mkdir(parents=True, exist_ok=True)
                probe_path = audio_dir / f"{stem}_probe.wav"
                background_path = audio_dir / f"{stem}_background.wav"
                generate_soundscape(
                    build_root / assets["ir_input_png"],
                    probe_path,
                    raspivoice_bin,
                    build_root,
                )
                generate_soundscape(
                    build_root / assets["background_input_png"],
                    background_path,
                    raspivoice_bin,
                    build_root,
                )
                matched_rms = normalize_wav_rms((probe_path, background_path))
                probe_wav = str(probe_path.relative_to(build_root))
                background_wav = str(background_path.relative_to(build_root))

            stimuli.append({
                "stimulus_id": stem,
                "source_ids": spec["sourceIds"],
                "target_ids": spec["targetIds"],
                "mapping_ids": spec["mappingIds"],
                "changed_count": spec["changedCount"],
                "source_family_split": normalized["split"],
                "target_choice_id": target_choice["choice_id"],
                "decoy_choice_id": decoy_choice["choice_id"],
                "response_choices": assets["choices"],
                "ir_probe_wav": probe_wav,
                "background_wav": background_wav,
                "wav_rms_int16": matched_rms,
                **{key: value for key, value in assets.items() if key != "choices"},
            })

        trials = make_schedule(stimuli, normalized, rng)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "task": "advanced-ir-ishihara-source-generalization",
            "session_id": session_id,
            "settings": normalized,
            "audio_generated": normalized["mode"] == "mixed",
            "audio_rms_normalized_within_pair": normalized["mode"] == "mixed",
            "audio_target_rms_int16": (
                TARGET_RMS_INT16 if normalized["mode"] == "mixed" else None
            ),
            "plate_width": PLATE_WIDTH,
            "plate_height": PLATE_HEIGHT,
            "audio_spatial_columns": AUDIO_WIDTH,
            "audio_spatial_rows": AUDIO_HEIGHT,
            "coordinate_mapping": "full-frame-normalized-no-crop",
            "sweep_duration_ms": round(SWEEP_DURATION_S * 1000),
            "sweep_repetitions": SWEEP_REPETITIONS,
            "inter_sweep_interval_ms": INTER_SWEEP_INTERVAL_MS,
            "stimulus_duration_ms": round(
                SWEEP_DURATION_S * 1000 * SWEEP_REPETITIONS
                + INTER_SWEEP_INTERVAL_MS * (SWEEP_REPETITIONS - 1)
            ),
            "mask_duration_ms": MASK_DURATION_MS,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "samples_per_column": SAMPLES_PER_COLUMN,
            "stimuli": stimuli,
            "trials": trials,
        }
        (build_root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        )
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(build_root), destination)

    final_manifest = json.loads(manifest_path.read_text())
    return manifest_path, final_manifest


def select_base_trials(families: list[dict], count: int, rng: random.Random) -> list[dict]:
    if not families:
        raise ValueError("source split has no families")
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    selected = []
    attempts = 0
    while len(selected) < count:
        attempts += 1
        if attempts > count * 500:
            raise RuntimeError("could not construct enough unique valid trials")
        length = (len(selected) % 3) + 1
        eligible = [family for family in families if length > 1 or family["familySize"] >= 4]
        chosen = [rng.choice(eligible) for _ in range(length)]
        source_ids = [family["sourceId"] for family in chosen]
        target_ids = []
        for family in chosen:
            if length > 1 and rng.random() < 0.28:
                target_ids.append(family["sourceId"])
            else:
                target_ids.append(rng.choice(family["changedTargetIds"]))
        if target_ids == source_ids:
            position = rng.randrange(length)
            target_ids[position] = rng.choice(chosen[position]["changedTargetIds"])
        signature = (tuple(source_ids), tuple(target_ids))
        if signature in seen:
            continue
        choice_targets = choose_interpretations(chosen, target_ids, rng)
        if choice_targets is None:
            continue
        seen.add(signature)
        mapping_ids = [
            f"{source_id}--{target_id}"
            for source_id, target_id in zip(source_ids, target_ids)
        ]
        selected.append({
            "sourceIds": source_ids,
            "targetIds": target_ids,
            "mappingIds": mapping_ids,
            "changedCount": sum(
                source != target for source, target in zip(source_ids, target_ids)
            ),
            "choiceTargets": choice_targets,
        })
    return selected


def choose_interpretations(
    families: list[dict],
    target_ids: list[str],
    rng: random.Random,
) -> list[list[str]] | None:
    source_ids = [family["sourceId"] for family in families]
    outcomes = [
        [family["sourceId"], *family["changedTargetIds"]]
        for family in families
    ]
    target = tuple(target_ids)
    decoy = tuple(source_ids)
    candidates = [
        combination
        for combination in itertools.product(*outcomes)
        if combination not in {target, decoy}
    ]
    if len(candidates) < 2:
        return None

    target_changes = sum(a != b for a, b in zip(source_ids, target))
    target_segment_count = sum(len(GEOMETRY_SEGMENTS[item]) for item in target)

    def score(candidate: tuple[str, ...]) -> tuple[float, ...]:
        changed = sum(a != b for a, b in zip(source_ids, candidate))
        hamming = sum(a != b for a, b in zip(candidate, target))
        segment_count = sum(len(GEOMETRY_SEGMENTS[item]) for item in candidate)
        return (
            abs(changed - target_changes),
            abs(segment_count - target_segment_count),
            hamming,
            rng.random(),
        )

    candidates.sort(key=score)
    interpretations = [target, decoy, candidates[0], candidates[1]]
    rng.shuffle(interpretations)
    return [list(item) for item in interpretations]


def make_schedule(stimuli: list[dict], settings: dict, rng: random.Random) -> list[dict]:
    if settings["mode"] == "visual-only":
        ordered = list(stimuli)
        rng.shuffle(ordered)
        return [
            trial_record(index, stimulus, "visual-only", None)
            for index, stimulus in enumerate(ordered, start=1)
        ]

    pairs = []
    conditions = ["visible-composite" if index % 2 == 0 else "ir-composite"
                  for index in range(len(stimuli))]
    rng.shuffle(conditions)
    for pair_index, (stimulus, first_condition) in enumerate(
        zip(stimuli, conditions), start=1
    ):
        second_condition = (
            "ir-composite" if first_condition == "visible-composite"
            else "visible-composite"
        )
        pair_id = f"pair-{pair_index:03d}-{stimulus['stimulus_id']}"
        pairs.append((
            trial_record(0, stimulus, first_condition, pair_id, 1),
            trial_record(0, stimulus, second_condition, pair_id, 2),
        ))
    schedule = [first for first, _second in pairs] + [second for _first, second in pairs]
    for index, trial in enumerate(schedule, start=1):
        trial["trial_index"] = index
    return schedule


def trial_record(
    index: int,
    stimulus: dict,
    condition: str,
    pair_id: str | None,
    pair_position: int | None = None,
) -> dict:
    if condition == "ir-composite":
        plate = stimulus["ir_plate_png"]
        wav = stimulus["ir_probe_wav"]
        audio_content = "diagnostic-ir-probe-plus-background-carrier"
    elif condition == "visible-composite":
        plate = stimulus["visual_plate_png"]
        wav = stimulus["background_wav"]
        audio_content = "background-only-carrier"
    else:
        plate = stimulus["visual_plate_png"]
        wav = None
        audio_content = "none"
    return {
        "trial_index": index,
        "stimulus_id": stimulus["stimulus_id"],
        "condition": condition,
        "pair_id": pair_id,
        "pair_position": pair_position,
        "plate_png": plate,
        "audio_wav": wav,
        "audio_content": audio_content,
    }


def manifest_is_complete(manifest: dict, root: Path) -> bool:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return False
    for stimulus in manifest.get("stimuli", []):
        for key in (
            "ir_plate_png", "visual_plate_png", "ir_input_png",
            "background_input_png",
        ):
            if not (root / stimulus[key]).is_file():
                return False
        for choice in stimulus.get("response_choices", []):
            if not (root / choice["png"]).is_file():
                return False
        for key in ("ir_probe_wav", "background_wav"):
            if not stimulus.get(key):
                continue
            path = root / stimulus[key]
            try:
                validate_wav(path)
            except RuntimeError:
                return False
            if manifest.get("audio_rms_normalized_within_pair"):
                target = manifest.get("audio_target_rms_int16")
                if not isinstance(target, (int, float)):
                    return False
                if abs(wav_rms_int16(path) - target) > 1.0:
                    return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("train", "test"), default="train")
    parser.add_argument("--mode", choices=("mixed", "visual-only"), default="visual-only")
    parser.add_argument("--trials", type=int, default=12)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "advanced_sessions")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    path, _manifest = prepare_session(
        {
            "split": arguments.split,
            "mode": arguments.mode,
            "trialCount": arguments.trials,
            "seed": arguments.seed,
        },
        arguments.out,
    )
    print(path)
