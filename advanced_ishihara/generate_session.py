#!/usr/bin/env python3
"""Generate and cache only the assets required by one advanced session."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
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
    AUDIO_NORMALIZATION_METHOD,
    CARRIER_TARGET_RMS_INT16,
    PEAK_CEILING_DBFS,
    SAMPLE_RATE_HZ,
    SAMPLES_PER_COLUMN,
    SWEEP_DURATION_S,
    apply_carrier_referenced_gain,
    default_raspivoice_bin,
    generate_soundscape,
    validate_wav,
    wav_peak_int16,
    wav_rms_int16,
)

SCHEMA_VERSION = 6
RENDER_VERSION = 2
AUDIO_RENDER_VERSION = 2
DIFFICULTY_MODEL_VERSION = "estimated-v1"
DIFFICULTY_COMPONENT_NAMES = (
    "glyph_load",
    "diagnostic_subtlety",
    "alternative_foil_similarity",
    "family_ambiguity",
)
DIFFICULTY_COMPONENT_WEIGHTS = {
    "glyph_load": 0.4,
    "diagnostic_subtlety": 0.3,
    "alternative_foil_similarity": 0.2,
    "family_ambiguity": 0.1,
}
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
    if split not in {"train", "test"}:
        raise ValueError("split must be train or test")

    legacy_mode = settings.get("mode")
    signal_mode = settings.get("signalMode")
    if signal_mode is None:
        signal_mode = {
            None: "mixed",
            "visual-only": "visual",
            "mixed": "paired",
        }.get(legacy_mode)
    if signal_mode not in {"visual", "ir", "mixed", "paired"}:
        raise ValueError("signalMode must be visual, ir, mixed, or paired")

    base_count_value = settings.get(
        "baseStimulusCount", settings.get("uniqueStimulusCount"),
    )
    if base_count_value is None and "trialCount" in settings:
        legacy_trial_count = _coerce_integer(settings["trialCount"], "trialCount")
        if legacy_mode == "mixed":
            if legacy_trial_count % 2:
                raise ValueError("legacy mixed sessions require an even trialCount")
            base_count_value = legacy_trial_count // 2
        else:
            base_count_value = legacy_trial_count
    if base_count_value is None:
        base_count_value = 12
    base_count = _coerce_integer(
        base_count_value, "baseStimulusCount",
    )
    if not 4 <= base_count <= 96:
        raise ValueError("baseStimulusCount must be between 4 and 96")

    glyph_composition = str(settings.get("glyphComposition", "automatic"))
    if glyph_composition not in {"automatic", "1", "2", "3"}:
        raise ValueError("glyphComposition must be automatic, 1, 2, or 3")

    progression = settings.get("progression", "mixed")
    if progression not in {"growing", "mixed"}:
        raise ValueError("progression must be growing or mixed")

    feedback_enabled = settings.get("feedbackEnabled", False)
    if not isinstance(feedback_enabled, bool):
        raise ValueError("feedbackEnabled must be a boolean")

    seed = _coerce_integer(settings.get("seed", 1729), "seed")
    if not 0 <= seed <= 0xFFFFFFFF:
        raise ValueError("seed must be an unsigned 32-bit integer")

    return {
        "split": split,
        "signalMode": signal_mode,
        "baseStimulusCount": base_count,
        "glyphComposition": glyph_composition,
        "progression": progression,
        "feedbackEnabled": feedback_enabled,
        "seed": seed,
        "schemaVersion": SCHEMA_VERSION,
    }


def _coerce_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        integer = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be an integer") from error
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, str) and str(integer) != value.strip():
        raise ValueError(f"{field_name} must be an integer")
    return integer


def glyph_count_quotas(
    base_stimulus_count: int,
    glyph_composition: str,
    seed: int,
) -> dict[int, int]:
    """Return exact glyph-count quotas for a session.

    Automatic quotas differ by at most one. Remainders are assigned cyclically
    over 1, 2, and 3 glyphs, beginning at ``seed % 3`` so the allocation is
    reproducible without consuming session RNG state.
    """
    if glyph_composition != "automatic":
        glyph_count = int(glyph_composition)
        return {
            length: base_stimulus_count if length == glyph_count else 0
            for length in (1, 2, 3)
        }
    base, remainder = divmod(base_stimulus_count, 3)
    quotas = {length: base for length in (1, 2, 3)}
    start = seed % 3
    lengths = (1, 2, 3)
    for offset in range(remainder):
        quotas[lengths[(start + offset) % 3]] += 1
    return quotas


def derive_seed(seed: int, stream_name: str) -> int:
    digest = hashlib.sha256(f"{seed}:{stream_name}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def prepare_session(
    settings: dict,
    output_root: Path,
    repo_root: Path = REPO_ROOT,
) -> tuple[Path, dict]:
    normalized = normalize_settings(settings)
    grammar = load_grammar(repo_root)
    cache_identity = {
        **normalized,
        "catalogVersion": grammar.get("catalogVersion", 1),
        "renderVersion": RENDER_VERSION,
        "audioRenderVersion": AUDIO_RENDER_VERSION,
    }
    session_key = hashlib.sha256(
        json.dumps(cache_identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    session_id = f"advanced-{session_key}"
    destination = output_root / session_id
    manifest_path = destination / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        if manifest_is_complete(manifest, destination):
            return manifest_path, manifest

    output_root.mkdir(parents=True, exist_ok=True)
    selection_rng = random.Random(derive_seed(normalized["seed"], "selection-v1"))
    foil_rng = random.Random(derive_seed(normalized["seed"], "foils-v1"))
    schedule_rng = random.Random(derive_seed(normalized["seed"], "schedule-v1"))
    families = [
        family
        for family in grammar["sourceFamilies"]
        if family["split"] == normalized["split"]
    ]
    quotas = glyph_count_quotas(
        normalized["baseStimulusCount"],
        normalized["glyphComposition"],
        normalized["seed"],
    )
    glyph_lengths = [
        glyph_count
        for glyph_count in (1, 2, 3)
        for _index in range(quotas[glyph_count])
    ]
    selection_rng.shuffle(glyph_lengths)
    base_specs = select_base_trials(
        families,
        normalized["baseStimulusCount"],
        selection_rng,
        glyph_lengths=glyph_lengths,
        foil_rng=foil_rng,
    )

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
                derive_seed(normalized["seed"], f"render-v1:{index}"),
            )
            target_choice = next(
                item for item in assets["choices"]
                if item["target_ids"] == spec["targetIds"]
            )
            decoy_choice = next(
                item for item in assets["choices"]
                if item["target_ids"] == spec["sourceIds"]
            )

            audio_assets = {
                "ir_probe_wav": None,
                "background_wav": None,
                "wav_rms_int16": None,
                "wav_peak_int16": None,
                "audio_normalization": None,
            }
            if normalized["signalMode"] in {"ir", "paired"}:
                audio_assets = generate_counterfactual_audio_assets(
                    assets,
                    build_root,
                    stem,
                    raspivoice_bin,
                    retain_probe=True,
                    retain_background=normalized["signalMode"] == "paired",
                )

            difficulty = estimate_difficulty(
                spec["families"],
                spec["targetIds"],
                spec["choiceTargets"],
                source_pixel_count=assets["source_pixel_count"],
                diagnostic_pixel_count=assets["diagnostic_pixel_count"],
            )
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
                **audio_assets,
                "transformation_signature": spec["transformationSignature"],
                "mapping_repetition_index": spec["mappingRepetitionIndex"],
                "estimated_difficulty_score": difficulty["score"],
                "difficulty_components": difficulty["components"],
                "difficulty_model_version": DIFFICULTY_MODEL_VERSION,
                "difficulty_inputs": difficulty["inputs"],
                **{key: value for key, value in assets.items() if key != "choices"},
            })

        assign_difficulty_ranks(stimuli)
        mixed_balance = None
        if normalized["signalMode"] == "mixed":
            assign_mixed_conditions(stimuli, normalized["seed"])
            generate_mixed_audio_assets(
                stimuli, build_root, raspivoice_bin,
            )
            mixed_balance = summarize_mixed_condition_balance(stimuli)
        trials = make_schedule(stimuli, normalized, schedule_rng)
        difficulty_summary = summarize_difficulty(stimuli)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "catalog_version": grammar.get("catalogVersion", 1),
            "render_version": RENDER_VERSION,
            "audio_render_version": AUDIO_RENDER_VERSION,
            "task": "advanced-ir-ishihara-source-generalization",
            "session_id": session_id,
            "settings": normalized,
            "base_stimulus_count": len(stimuli),
            "total_presentation_count": len(trials),
            "glyph_count_distribution": {
                str(glyph_count): quotas[glyph_count]
                for glyph_count in (1, 2, 3)
            },
            "condition_distribution": {
                condition: sum(trial["condition"] == condition for trial in trials)
                for condition in (
                    "visual_silent", "visual_background_audio", "ir_audio",
                )
                if any(trial["condition"] == condition for trial in trials)
            },
            "comparison_design": (
                "distinct-stimulus-carrier-controlled-between-condition"
                if normalized["signalMode"] == "mixed"
                else "within-stimulus-repeated"
                if normalized["signalMode"] == "paired"
                else "single-condition"
            ),
            "stimuli_repeated_across_conditions": normalized["signalMode"] == "paired",
            "condition_assignment": (
                {
                    "method": "provisional-structural-matching-v1",
                    "matching_priority": (
                        "same glyph count, difficulty stratum, changed count, structural score"
                    ),
                    "pair_orientation": (
                        "seeded alternating orientation; boundary pairs optimized for stratum balance"
                    ),
                    "odd_remainder": (
                        "visual_background_audio when seed is even; ir_audio when seed is odd"
                    ),
                    "audio_matching": (
                        "carrier-referenced shared gain; composite total RMS not equalized"
                    ),
                    **mixed_balance,
                }
                if mixed_balance is not None else None
            ),
            "feedback_enabled": normalized["feedbackEnabled"],
            "difficulty_model_version": DIFFICULTY_MODEL_VERSION,
            "difficulty_component_names": list(DIFFICULTY_COMPONENT_NAMES),
            "difficulty_component_weights": DIFFICULTY_COMPONENT_WEIGHTS,
            "difficulty_summary": difficulty_summary,
            "audio_generated": normalized["signalMode"] in {"ir", "mixed", "paired"},
            "audio_normalization_method": (
                AUDIO_NORMALIZATION_METHOD
                if normalized["signalMode"] in {"ir", "mixed", "paired"}
                else None
            ),
            "audio_carrier_target_rms_int16": (
                CARRIER_TARGET_RMS_INT16
                if normalized["signalMode"] in {"ir", "mixed", "paired"}
                else None
            ),
            "audio_peak_ceiling_dbfs": (
                PEAK_CEILING_DBFS
                if normalized["signalMode"] in {"ir", "mixed", "paired"}
                else None
            ),
            "audio_counterfactual_shared_gain": (
                normalized["signalMode"] in {"ir", "mixed", "paired"}
            ),
            "audio_whole_file_rms_equalized": False,
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


def select_base_trials(
    families: list[dict],
    count: int,
    rng: random.Random,
    glyph_lengths: list[int] | None = None,
    foil_rng: random.Random | None = None,
) -> list[dict]:
    if not families:
        raise ValueError("source split has no families")
    if glyph_lengths is None:
        glyph_lengths = [(index % 3) + 1 for index in range(count)]
    if len(glyph_lengths) != count or any(
        length not in {1, 2, 3} for length in glyph_lengths
    ):
        raise ValueError("glyph_lengths must contain one valid length per stimulus")
    foil_rng = foil_rng or rng

    repetition_counts: dict[str, int] = {}
    selected = []
    one_glyph_catalog = [
        (family, target_id)
        for family in families
        if family["familySize"] >= 4
        for target_id in family["changedTargetIds"]
    ]
    rng.shuffle(one_glyph_catalog)
    one_glyph_cursor = 0

    for length in glyph_lengths:
        eligible = [family for family in families if length > 1 or family["familySize"] >= 4]
        if length == 1:
            family, target_id = one_glyph_catalog[
                one_glyph_cursor % len(one_glyph_catalog)
            ]
            one_glyph_cursor += 1
            chosen = [family]
            source_ids = [family["sourceId"]]
            target_ids = [target_id]
            choice_targets = choose_interpretations(chosen, target_ids, foil_rng)
            if choice_targets is None:  # Guard the grammar invariant explicitly.
                raise RuntimeError("one-glyph family cannot provide four interpretations")
            signature = transformation_signature(source_ids, target_ids)
            repetition_counts[signature] = repetition_counts.get(signature, 0) + 1
            selected.append(_base_trial_record(
                chosen,
                source_ids,
                target_ids,
                choice_targets,
                signature,
                repetition_counts[signature],
            ))
            continue

        duplicate_candidate = None
        candidate = None
        # Prefer a new mapping signature. A forced one-glyph block can request
        # more instances than the finite mapping grammar contains, so after a
        # bounded search we intentionally reuse a mapping with a fresh plate
        # layout and response ordering.
        for _attempt in range(100):
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
            choice_targets = choose_interpretations(chosen, target_ids, foil_rng)
            if choice_targets is None:
                continue
            signature = transformation_signature(source_ids, target_ids)
            built = _base_trial_record(
                chosen,
                source_ids,
                target_ids,
                choice_targets,
                signature,
                repetition_counts.get(signature, 0) + 1,
            )
            if signature not in repetition_counts:
                candidate = built
                break
            if duplicate_candidate is None:
                duplicate_candidate = built
        candidate = candidate or duplicate_candidate
        if candidate is None:
            raise RuntimeError("could not construct a valid trial")
        signature = candidate["transformationSignature"]
        repetition_counts[signature] = repetition_counts.get(signature, 0) + 1
        candidate["mappingRepetitionIndex"] = repetition_counts[signature]
        selected.append(candidate)
    return selected


def _base_trial_record(
    families: list[dict],
    source_ids: list[str],
    target_ids: list[str],
    choice_targets: list[list[str]],
    signature: str,
    repetition_index: int,
) -> dict:
    return {
        "families": families,
        "sourceIds": source_ids,
        "targetIds": target_ids,
        "mappingIds": [
            f"{source_id}--{target_id}"
            for source_id, target_id in zip(source_ids, target_ids)
        ],
        "changedCount": sum(
            source != target for source, target in zip(source_ids, target_ids)
        ),
        "choiceTargets": choice_targets,
        "transformationSignature": signature,
        "mappingRepetitionIndex": repetition_index,
    }


def transformation_signature(source_ids: list[str], target_ids: list[str]) -> str:
    return "|".join(
        f"{source_id}--{target_id}"
        for source_id, target_id in zip(source_ids, target_ids)
    )


def estimate_difficulty(
    families: list[dict],
    target_ids: list[str],
    choice_targets: list[list[str]],
    source_pixel_count: int | None = None,
    diagnostic_pixel_count: int | None = None,
) -> dict:
    """Return the preregistrable v1 estimate and its auditable inputs."""
    glyph_count = len(families)
    glyph_load = (glyph_count - 1) / 2

    source_ids = [family["sourceId"] for family in families]
    if source_pixel_count is None:
        source_pixel_count = sum(len(GEOMETRY_SEGMENTS[item]) for item in source_ids)
    if diagnostic_pixel_count is None:
        diagnostic_pixel_count = sum(
            len(set(GEOMETRY_SEGMENTS[target]) - set(GEOMETRY_SEGMENTS[source]))
            for source, target in zip(source_ids, target_ids)
        )
    diagnostic_subtlety = 1 - (
        diagnostic_pixel_count / (source_pixel_count + diagnostic_pixel_count)
    )

    alternative_foils = [
        choice
        for choice in choice_targets
        if choice not in (target_ids, source_ids)
    ]
    alternative_foil_similarities = [
        _interpretation_similarity(target_ids, choice)
        for choice in alternative_foils
    ]
    alternative_foil_similarity = max(alternative_foil_similarities)

    outcome_space_size = math.prod(family["familySize"] for family in families)
    family_ambiguity = math.log(outcome_space_size) / math.log(11**3)

    components = {
        "glyph_load": glyph_load,
        "diagnostic_subtlety": diagnostic_subtlety,
        "alternative_foil_similarity": alternative_foil_similarity,
        "family_ambiguity": family_ambiguity,
    }
    components = {
        name: round(max(0.0, min(1.0, value)), 6)
        for name, value in components.items()
    }
    score = round(100 * sum(
        DIFFICULTY_COMPONENT_WEIGHTS[name] * value
        for name, value in components.items()
    ), 4)
    return {
        "score": score,
        "components": components,
        "inputs": {
            "source_pixel_count": source_pixel_count,
            "diagnostic_pixel_count": diagnostic_pixel_count,
            "outcome_space_size": outcome_space_size,
            "alternative_foil_similarities": [
                round(value, 6) for value in alternative_foil_similarities
            ],
        },
    }


def _interpretation_similarity(target: list[str], choice: list[str]) -> float:
    similarities = []
    for target_id, choice_id in zip(target, choice):
        target_segments = set(GEOMETRY_SEGMENTS[target_id])
        choice_segments = set(GEOMETRY_SEGMENTS[choice_id])
        similarities.append(
            len(target_segments & choice_segments)
            / len(target_segments | choice_segments)
        )
    return sum(similarities) / len(similarities)


def assign_difficulty_ranks(stimuli: list[dict]) -> None:
    ordered = sorted(
        stimuli,
        key=lambda item: (
            item["estimated_difficulty_score"],
            item["transformation_signature"],
            item["mapping_repetition_index"],
            item["stimulus_id"],
        ),
    )
    count = len(ordered)
    labels = ("easy", "moderate", "hard")
    for rank, stimulus in enumerate(ordered, start=1):
        stimulus["difficulty_rank"] = rank
        stimulus["difficulty_stratum"] = labels[min(2, (rank - 1) * 3 // count)]


def summarize_difficulty(stimuli: list[dict]) -> dict:
    scores = [item["estimated_difficulty_score"] for item in stimuli]
    return {
        "score_scale": "0-100",
        "minimum": min(scores),
        "maximum": max(scores),
        "mean": round(sum(scores) / len(scores), 4),
        "component_means": {
            name: round(sum(
                item["difficulty_components"][name] for item in stimuli
            ) / len(stimuli), 6)
            for name in DIFFICULTY_COMPONENT_NAMES
        },
        "stratum_counts": {
            label: sum(item["difficulty_stratum"] == label for item in stimuli)
            for label in ("easy", "moderate", "hard")
        },
    }


def assign_mixed_conditions(stimuli: list[dict], seed: int) -> None:
    """Match distinct puzzles structurally, then assign opposite conditions."""
    stratum_order = {"easy": 0, "moderate": 1, "hard": 2}
    by_glyph_count: dict[int, list[dict]] = {}
    for stimulus in stimuli:
        glyph_count = len(stimulus.get("source_ids", ())) or 1
        by_glyph_count.setdefault(glyph_count, []).append(stimulus)

    matched_pairs: list[tuple[dict, dict]] = []
    group_remainders = []
    for glyph_count in sorted(by_glyph_count):
        group = sorted(
            by_glyph_count[glyph_count],
            key=lambda item: (
                stratum_order.get(item.get("difficulty_stratum"), 1),
                item.get("changed_count", 0),
                item.get("estimated_difficulty_score", 0),
                item.get("difficulty_rank", 0),
                item.get("transformation_signature", item["stimulus_id"]),
                item["stimulus_id"],
            ),
        )
        if len(group) % 2:
            group_remainders.append(group.pop())
        matched_pairs.extend(
            (group[index], group[index + 1])
            for index in range(0, len(group), 2)
        )

    unmatched_remainder = None
    if len(group_remainders) == 1:
        unmatched_remainder = group_remainders[0]
    elif len(group_remainders) == 2:
        matched_pairs.append(tuple(group_remainders))
    elif len(group_remainders) == 3:
        # Pair the two structurally closest glyph-group remainders and leave
        # the third as the odd-N condition remainder.
        choices = []
        for first_index, second_index in itertools.combinations(range(3), 2):
            first = group_remainders[first_index]
            second = group_remainders[second_index]
            choices.append((
                (
                    abs(first.get("changed_count", 0) - second.get("changed_count", 0)),
                    abs(
                        first.get("estimated_difficulty_score", 0)
                        - second.get("estimated_difficulty_score", 0)
                    ),
                    abs(
                        len(first.get("source_ids", ()))
                        - len(second.get("source_ids", ()))
                    ),
                    first["stimulus_id"],
                    second["stimulus_id"],
                ),
                first_index,
                second_index,
            ))
        _score, first_index, second_index = min(choices)
        matched_pairs.append((
            group_remainders[first_index], group_remainders[second_index],
        ))
        unmatched_remainder = next(
            item for index, item in enumerate(group_remainders)
            if index not in {first_index, second_index}
        )

    matched_pairs.sort(key=lambda pair: (
        min(item.get("difficulty_rank", 0) for item in pair),
        pair[0]["stimulus_id"],
        pair[1]["stimulus_id"],
    ))
    initial_orientation = (seed // 2) % 2
    preferred_orientations = [
        (pair_index + initial_orientation) % 2 == 0
        for pair_index in range(len(matched_pairs))
    ]
    sensitive_pair_indexes = [
        pair_index
        for pair_index, pair in enumerate(matched_pairs)
        if (
            len(pair[0].get("source_ids", ())) != len(pair[1].get("source_ids", ()))
            or pair[0].get("difficulty_stratum") != pair[1].get("difficulty_stratum")
        )
    ]
    remainder_condition = (
        "visual_background_audio" if seed % 2 == 0 else "ir_audio"
    )

    # Only structurally cross-group pairs affect per-glyph or per-stratum
    # balance. Exhaust their small orientation space and retain the seeded
    # alternating orientation as the deterministic final tie-breaker.
    best_orientations = preferred_orientations
    best_objective = None
    for orientation_bits in itertools.product(
        (False, True), repeat=len(sensitive_pair_indexes),
    ):
        orientations = list(preferred_orientations)
        for pair_index, visual_first in zip(
            sensitive_pair_indexes, orientation_bits,
        ):
            orientations[pair_index] = visual_first
        assigned = []
        for pair_index, visual_first in enumerate(orientations):
            conditions = (
                ("visual_background_audio", "ir_audio")
                if visual_first else ("ir_audio", "visual_background_audio")
            )
            assigned.extend(zip(matched_pairs[pair_index], conditions))
        if unmatched_remainder is not None:
            assigned.append((unmatched_remainder, remainder_condition))
        stratum_differences = []
        for stratum in ("easy", "moderate", "hard"):
            stratum_conditions = [
                condition
                for stimulus, condition in assigned
                if stimulus["difficulty_stratum"] == stratum
            ]
            stratum_differences.append(abs(
                stratum_conditions.count("visual_background_audio")
                - stratum_conditions.count("ir_audio")
            ))
        glyph_differences = []
        for glyph_count in sorted(by_glyph_count):
            glyph_conditions = [
                condition
                for stimulus, condition in assigned
                if (len(stimulus.get("source_ids", ())) or 1) == glyph_count
            ]
            glyph_differences.append(abs(
                glyph_conditions.count("visual_background_audio")
                - glyph_conditions.count("ir_audio")
            ))
        visual_scores = [
            stimulus.get("estimated_difficulty_score", 0)
            for stimulus, condition in assigned
            if condition == "visual_background_audio"
        ]
        ir_scores = [
            stimulus.get("estimated_difficulty_score", 0)
            for stimulus, condition in assigned
            if condition == "ir_audio"
        ]
        score_mean_gap = abs(
            sum(visual_scores) / len(visual_scores)
            - sum(ir_scores) / len(ir_scores)
        )
        objective = (
            max(glyph_differences, default=0),
            max(stratum_differences, default=0),
            sum(glyph_differences),
            sum(stratum_differences),
            score_mean_gap,
            abs(sum(orientations) * 2 - len(orientations)),
            sum(
                orientations[index] != preferred_orientations[index]
                for index in sensitive_pair_indexes
            ),
            orientation_bits,
        )
        if best_objective is None or objective < best_objective:
            best_objective = objective
            best_orientations = orientations

    for pair_index, (first, second) in enumerate(matched_pairs):
        visual_first = best_orientations[pair_index]
        conditions = (
            ("visual_background_audio", "ir_audio")
            if visual_first else ("ir_audio", "visual_background_audio")
        )
        match_id = f"mixed-match-{pair_index + 1:03d}"
        score_gap = round(abs(
            first.get("estimated_difficulty_score", 0)
            - second.get("estimated_difficulty_score", 0)
        ), 4)
        for position, (stimulus, condition) in enumerate(
            zip((first, second), conditions), start=1,
        ):
            stimulus["assigned_condition"] = condition
            stimulus["difficulty_match_id"] = match_id
            stimulus["difficulty_match_position"] = position
            stimulus["difficulty_match_score_gap"] = score_gap

    if unmatched_remainder is not None:
        unmatched_remainder["assigned_condition"] = remainder_condition
        unmatched_remainder["difficulty_match_id"] = None
        unmatched_remainder["difficulty_match_position"] = None
        unmatched_remainder["difficulty_match_score_gap"] = None


def generate_counterfactual_audio_assets(
    assets: dict,
    build_root: Path,
    stem: str,
    raspivoice_bin: Path,
    *,
    retain_probe: bool,
    retain_background: bool,
) -> dict:
    """Render required WAVs and apply one carrier-derived gain to all of them."""
    if not retain_probe and not retain_background:
        raise ValueError("at least one counterfactual WAV must be retained")
    audio_dir = build_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    probe_path = audio_dir / f"{stem}_probe.wav"
    background_path = audio_dir / f"{stem}_background.wav"
    if retain_probe:
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
    generated_paths = (
        (probe_path, background_path) if retain_probe else (background_path,)
    )
    normalization = apply_carrier_referenced_gain(
        generated_paths,
        background_path,
    )
    file_metrics = normalization.pop("files")
    role_paths = {"background_carrier": background_path}
    if retain_probe:
        role_paths["ir_probe"] = probe_path
    retained_by_role = {
        "ir_probe": retain_probe,
        "background_carrier": retain_background,
    }
    normalization["counterfactuals"] = {
        role: {
            **file_metrics[path.name],
            "retained": retained_by_role[role],
        }
        for role, path in role_paths.items()
    }

    retained_paths = [
        path
        for role, path in role_paths.items()
        if retained_by_role[role]
    ]
    if not retain_background:
        background_path.unlink()
    return {
        "ir_probe_wav": (
            str(probe_path.relative_to(build_root)) if retain_probe else None
        ),
        "background_wav": (
            str(background_path.relative_to(build_root))
            if retain_background else None
        ),
        "wav_rms_int16": {
            path.name: file_metrics[path.name]["final_rms_int16"]
            for path in retained_paths
        },
        "wav_peak_int16": {
            path.name: file_metrics[path.name]["final_peak_int16"]
            for path in retained_paths
        },
        "audio_normalization": normalization,
    }


def generate_mixed_audio_assets(
    stimuli: list[dict],
    build_root: Path,
    raspivoice_bin: Path,
) -> None:
    for stimulus in stimuli:
        stem = stimulus["stimulus_id"]
        condition = stimulus["assigned_condition"]
        if condition == "ir_audio":
            retain_probe = True
            retain_background = False
        elif condition == "visual_background_audio":
            retain_probe = False
            retain_background = True
        else:
            raise ValueError(f"unknown mixed condition: {condition}")
        stimulus.update(generate_counterfactual_audio_assets(
            stimulus,
            build_root,
            stem,
            raspivoice_bin,
            retain_probe=retain_probe,
            retain_background=retain_background,
        ))


def summarize_mixed_condition_balance(stimuli: list[dict]) -> dict:
    conditions = ("visual_background_audio", "ir_audio")

    def counts(items: list[dict]) -> dict[str, int]:
        return {
            condition: sum(item["assigned_condition"] == condition for item in items)
            for condition in conditions
        }

    condition_counts = counts(stimuli)
    by_stratum = {}
    for stratum in ("easy", "moderate", "hard"):
        members = [item for item in stimuli if item["difficulty_stratum"] == stratum]
        stratum_counts = counts(members)
        by_stratum[stratum] = {
            **stratum_counts,
            "absolute_difference": abs(
                stratum_counts[conditions[0]] - stratum_counts[conditions[1]]
            ),
        }

    by_glyph_count = {}
    for glyph_count in (1, 2, 3):
        members = [
            item for item in stimuli
            if len(item.get("source_ids", ())) == glyph_count
        ]
        glyph_counts = counts(members)
        by_glyph_count[str(glyph_count)] = {
            **glyph_counts,
            "absolute_difference": abs(
                glyph_counts[conditions[0]] - glyph_counts[conditions[1]]
            ),
        }

    score_means = {}
    for condition in conditions:
        scores = [
            item["estimated_difficulty_score"]
            for item in stimuli
            if item["assigned_condition"] == condition
        ]
        score_means[condition] = round(sum(scores) / len(scores), 4) if scores else None

    match_ids = sorted({
        item["difficulty_match_id"]
        for item in stimuli
        if item["difficulty_match_id"] is not None
    })
    score_gaps = []
    all_pairs_cross_condition = True
    for match_id in match_ids:
        pair = [item for item in stimuli if item["difficulty_match_id"] == match_id]
        all_pairs_cross_condition &= {
            item["assigned_condition"] for item in pair
        } == set(conditions)
        score_gaps.append(pair[0]["difficulty_match_score_gap"])

    return {
        "condition_counts": condition_counts,
        "global_absolute_difference": abs(
            condition_counts[conditions[0]] - condition_counts[conditions[1]]
        ),
        "by_difficulty_stratum": by_stratum,
        "by_glyph_count": by_glyph_count,
        "difficulty_score_means": score_means,
        "difficulty_score_mean_absolute_difference": (
            round(abs(score_means[conditions[0]] - score_means[conditions[1]]), 4)
            if all(value is not None for value in score_means.values()) else None
        ),
        "complete_difficulty_match_pairs": len(match_ids),
        "unmatched_remainder_count": len(stimuli) % 2,
        "all_complete_pairs_cross_condition": all_pairs_cross_condition,
        "mean_within_match_score_gap": (
            round(sum(score_gaps) / len(score_gaps), 4) if score_gaps else None
        ),
        "maximum_within_match_score_gap": (
            round(max(score_gaps), 4) if score_gaps else None
        ),
    }


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
    signal_mode = settings.get("signalMode")
    if signal_mode is None:
        if "mode" in settings:
            signal_mode = "paired" if settings.get("mode") == "mixed" else "visual"
        else:
            signal_mode = "mixed"
    progression = settings.get("progression", "mixed")
    ordered = list(stimuli)
    if progression == "growing":
        ordered.sort(key=lambda item: (
            item.get("estimated_difficulty_score", 0),
            item.get("transformation_signature", item["stimulus_id"]),
            item["stimulus_id"],
        ))
    elif progression == "mixed":
        rng.shuffle(ordered)
    else:
        raise ValueError("progression must be growing or mixed")

    if signal_mode in {"visual", "ir"}:
        condition = "visual_silent" if signal_mode == "visual" else "ir_audio"
        return [
            trial_record(
                index,
                stimulus,
                condition,
                None,
                response_choice_ids=_shuffled_choice_ids(stimulus, rng),
            )
            for index, stimulus in enumerate(ordered, start=1)
        ]
    if signal_mode == "mixed":
        if any("assigned_condition" not in stimulus for stimulus in stimuli):
            assign_mixed_conditions(stimuli, int(settings.get("seed", 1729)))
        return [
            trial_record(
                index,
                stimulus,
                stimulus["assigned_condition"],
                None,
                response_choice_ids=_shuffled_choice_ids(stimulus, rng),
            )
            for index, stimulus in enumerate(ordered, start=1)
        ]
    if signal_mode != "paired":
        raise ValueError("signalMode must be visual, ir, mixed, or paired")

    first_conditions = [
        "visual_background_audio" if index % 2 == 0 else "ir_audio"
        for index in range(len(ordered))
    ]
    rng.shuffle(first_conditions)
    pair_metadata = {}
    first_trials = []
    first_choice_orders = {}
    for pair_index, (stimulus, first_condition) in enumerate(zip(
        ordered, first_conditions,
    ), start=1):
        second_condition = (
            "ir_audio"
            if first_condition == "visual_background_audio"
            else "visual_background_audio"
        )
        pair_id = f"pair-{pair_index:03d}-{stimulus['stimulus_id']}"
        pair_order = (
            "visual-ir"
            if first_condition == "visual_background_audio"
            else "ir-visual"
        )
        choice_order = _shuffled_choice_ids(stimulus, rng)
        first_choice_orders[stimulus["stimulus_id"]] = choice_order
        pair_metadata[stimulus["stimulus_id"]] = {
            "pair_id": pair_id,
            "pair_order": pair_order,
            "second_condition": second_condition,
        }
        first_trials.append(trial_record(
            0,
            stimulus,
            first_condition,
            pair_id,
            pair_position=1,
            pair_order=pair_order,
            pair_pass=1,
            response_choice_ids=choice_order,
        ))

    second_order = (
        list(ordered)
        if progression == "growing"
        else _second_pass_order(ordered, rng)
    )
    second_trials = []
    stimulus_by_id = {item["stimulus_id"]: item for item in stimuli}
    for stimulus_reference in second_order:
        stimulus = stimulus_by_id[stimulus_reference["stimulus_id"]]
        metadata = pair_metadata[stimulus["stimulus_id"]]
        second_trials.append(trial_record(
            0,
            stimulus,
            metadata["second_condition"],
            metadata["pair_id"],
            pair_position=2,
            pair_order=metadata["pair_order"],
            pair_pass=2,
            response_choice_ids=_shuffled_choice_ids(
                stimulus,
                rng,
                avoid=first_choice_orders[stimulus["stimulus_id"]],
            ),
        ))

    schedule = first_trials + second_trials
    for index, trial in enumerate(schedule, start=1):
        trial["trial_index"] = index
    pair_positions: dict[str, list[int]] = {}
    for trial in schedule:
        pair_positions.setdefault(trial["pair_id"], []).append(trial["trial_index"])
    for trial in schedule:
        first_index, second_index = pair_positions[trial["pair_id"]]
        trial["pair_lag"] = second_index - first_index - 1
    return schedule


def _second_pass_order(stimuli: list[dict], rng: random.Random) -> list[dict]:
    if len(stimuli) <= 2:
        return list(reversed(stimuli))
    first_positions = {
        stimulus["stimulus_id"]: index
        for index, stimulus in enumerate(stimuli, start=1)
    }
    for _attempt in range(200):
        candidate = list(stimuli)
        rng.shuffle(candidate)
        if candidate == stimuli:
            continue
        if all(
            len(stimuli) + second_position
            - first_positions[stimulus["stimulus_id"]] - 1 >= 2
            for second_position, stimulus in enumerate(candidate, start=1)
        ):
            return candidate
    # A right rotation by three satisfies the two-intervening-trial minimum for
    # all supported block sizes (which begin at four stimuli).
    return stimuli[-3:] + stimuli[:-3]


def _shuffled_choice_ids(
    stimulus: dict,
    rng: random.Random,
    avoid: list[str] | None = None,
) -> list[str]:
    choice_ids = [
        item["choice_id"] for item in stimulus.get("response_choices", [])
    ]
    rng.shuffle(choice_ids)
    if avoid is not None and choice_ids == avoid and len(choice_ids) > 1:
        choice_ids = choice_ids[1:] + choice_ids[:1]
    return choice_ids


def trial_record(
    index: int,
    stimulus: dict,
    condition: str,
    pair_id: str | None,
    pair_position: int | None = None,
    pair_order: str | None = None,
    pair_pass: int | None = None,
    response_choice_ids: list[str] | None = None,
) -> dict:
    if condition == "ir_audio":
        plate = stimulus["ir_plate_png"]
        wav = stimulus["ir_probe_wav"]
        audio_content = "diagnostic-ir-probe-plus-background-carrier"
    elif condition == "visual_background_audio":
        plate = stimulus["visual_plate_png"]
        wav = stimulus["background_wav"]
        audio_content = "background-only-carrier"
    elif condition == "visual_silent":
        plate = stimulus["visual_plate_png"]
        wav = None
        audio_content = "none"
    else:
        raise ValueError(f"unknown trial condition: {condition}")
    return {
        "trial_index": index,
        "stimulus_id": stimulus["stimulus_id"],
        "condition": condition,
        "pair_id": pair_id,
        "pair_position": pair_position,
        "pair_order": pair_order,
        "pair_pass": pair_pass,
        "pair_lag": None,
        "plate_png": plate,
        "audio_wav": wav,
        "audio_content": audio_content,
        "response_choice_ids": response_choice_ids or [],
        "target_choice_id": stimulus.get("target_choice_id"),
        "decoy_choice_id": stimulus.get("decoy_choice_id"),
        "transformation_signature": stimulus.get("transformation_signature"),
        "mapping_repetition_index": stimulus.get("mapping_repetition_index"),
        "estimated_difficulty_score": stimulus.get("estimated_difficulty_score"),
        "difficulty_components": stimulus.get("difficulty_components"),
        "difficulty_model_version": stimulus.get("difficulty_model_version"),
        "difficulty_rank": stimulus.get("difficulty_rank"),
        "difficulty_stratum": stimulus.get("difficulty_stratum"),
        "difficulty_match_id": stimulus.get("difficulty_match_id"),
        "difficulty_match_position": stimulus.get("difficulty_match_position"),
        "difficulty_match_score_gap": stimulus.get("difficulty_match_score_gap"),
    }


def manifest_is_complete(manifest: dict, root: Path) -> bool:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return False
    if manifest.get("render_version") != RENDER_VERSION:
        return False
    if manifest.get("audio_render_version") != AUDIO_RENDER_VERSION:
        return False
    signal_mode = manifest.get("settings", {}).get("signalMode")
    audio_expected = signal_mode in {"ir", "mixed", "paired"}
    if manifest.get("audio_generated") != audio_expected:
        return False
    if manifest.get("audio_whole_file_rms_equalized") is not False:
        return False
    if manifest.get("audio_counterfactual_shared_gain") != audio_expected:
        return False
    expected_method = AUDIO_NORMALIZATION_METHOD if audio_expected else None
    expected_target = CARRIER_TARGET_RMS_INT16 if audio_expected else None
    expected_ceiling = PEAK_CEILING_DBFS if audio_expected else None
    if manifest.get("audio_normalization_method") != expected_method:
        return False
    if manifest.get("audio_carrier_target_rms_int16") != expected_target:
        return False
    if manifest.get("audio_peak_ceiling_dbfs") != expected_ceiling:
        return False
    for stimulus in manifest.get("stimuli", []):
        if (
            signal_mode == "mixed"
            and stimulus.get("assigned_condition")
            not in {"visual_background_audio", "ir_audio"}
        ):
            return False
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
                if key == "ir_probe_wav" and signal_mode in {"ir", "paired"}:
                    return False
                if key == "background_wav" and signal_mode == "paired":
                    return False
                if (
                    signal_mode == "mixed"
                    and key == "ir_probe_wav"
                    and stimulus.get("assigned_condition") == "ir_audio"
                ):
                    return False
                if (
                    signal_mode == "mixed"
                    and key == "background_wav"
                    and stimulus.get("assigned_condition") == "visual_background_audio"
                ):
                    return False
                continue
            path = root / stimulus[key]
            try:
                validate_wav(path)
            except RuntimeError:
                return False
        if not _stimulus_audio_is_complete(stimulus, signal_mode, root):
            return False
    stimuli_by_id = {
        stimulus.get("stimulus_id"): stimulus
        for stimulus in manifest.get("stimuli", [])
    }
    if signal_mode == "mixed":
        mixed_trial_ids = [
            trial.get("stimulus_id") for trial in manifest.get("trials", [])
        ]
        if len(mixed_trial_ids) != len(stimuli_by_id):
            return False
        if set(mixed_trial_ids) != set(stimuli_by_id):
            return False
    for trial in manifest.get("trials", []):
        stimulus = stimuli_by_id.get(trial.get("stimulus_id"))
        if stimulus is None:
            return False
        condition = trial.get("condition")
        if condition == "visual_silent":
            expected_plate = stimulus.get("visual_plate_png")
            expected_wav = None
        elif condition == "visual_background_audio":
            expected_plate = stimulus.get("visual_plate_png")
            expected_wav = stimulus.get("background_wav")
        elif condition == "ir_audio":
            expected_plate = stimulus.get("ir_plate_png")
            expected_wav = stimulus.get("ir_probe_wav")
        else:
            return False
        if trial.get("plate_png") != expected_plate:
            return False
        if trial.get("audio_wav") != expected_wav:
            return False
        if expected_wav is not None and not (root / expected_wav).is_file():
            return False
    return True


def _stimulus_audio_is_complete(
    stimulus: dict,
    signal_mode: str,
    root: Path,
) -> bool:
    assigned_condition = stimulus.get("assigned_condition")
    probe_required = (
        signal_mode in {"ir", "paired"}
        or signal_mode == "mixed" and assigned_condition == "ir_audio"
    )
    background_required = (
        signal_mode == "paired"
        or signal_mode == "mixed"
        and assigned_condition == "visual_background_audio"
    )
    if not probe_required and not background_required:
        return (
            stimulus.get("audio_normalization") is None
            and stimulus.get("wav_rms_int16") is None
            and stimulus.get("wav_peak_int16") is None
        )

    normalization = stimulus.get("audio_normalization")
    if not isinstance(normalization, dict):
        return False
    if normalization.get("method") != AUDIO_NORMALIZATION_METHOD:
        return False
    if normalization.get("carrier_target_rms_int16") != CARRIER_TARGET_RMS_INT16:
        return False
    if normalization.get("peak_ceiling_dbfs") != PEAK_CEILING_DBFS:
        return False
    raw_carrier_rms = normalization.get("raw_carrier_rms_int16")
    requested_gain = normalization.get("requested_gain_linear")
    shared_gain = normalization.get("shared_gain_linear")
    shared_gain_db = normalization.get("shared_gain_db")
    peak_ceiling = normalization.get("peak_ceiling_int16")
    if not all(isinstance(value, (int, float)) for value in (
        raw_carrier_rms, requested_gain, shared_gain, shared_gain_db,
        peak_ceiling,
    )):
        return False
    if raw_carrier_rms <= 0 or shared_gain <= 0 or shared_gain > requested_gain:
        return False
    if not math.isclose(
        requested_gain,
        CARRIER_TARGET_RMS_INT16 / raw_carrier_rms,
        rel_tol=1e-12,
    ):
        return False
    if not math.isclose(
        shared_gain_db,
        20 * math.log10(shared_gain),
        rel_tol=1e-12,
    ):
        return False
    expected_peak_ceiling = math.floor(
        32_767 * (10 ** (PEAK_CEILING_DBFS / 20)),
    )
    if peak_ceiling != expected_peak_ceiling:
        return False
    peak_limited = normalization.get("peak_limited")
    if not isinstance(peak_limited, bool):
        return False
    if peak_limited != (shared_gain < requested_gain):
        return False

    counterfactuals = normalization.get("counterfactuals")
    if not isinstance(counterfactuals, dict):
        return False
    background_metrics = counterfactuals.get("background_carrier")
    if not _audio_metrics_are_valid(
        background_metrics,
        shared_gain,
        peak_ceiling,
        retained=background_required,
    ):
        return False
    background_rms = background_metrics["final_rms_int16"]
    if background_rms > CARRIER_TARGET_RMS_INT16 + 1.0:
        return False
    if not peak_limited and abs(
        background_rms - CARRIER_TARGET_RMS_INT16
    ) > 1.0:
        return False

    probe_metrics = counterfactuals.get("ir_probe")
    if probe_required:
        if not _audio_metrics_are_valid(
            probe_metrics,
            shared_gain,
            peak_ceiling,
            retained=True,
        ):
            return False
    elif probe_metrics is not None:
        return False

    rms_manifest = stimulus.get("wav_rms_int16")
    peak_manifest = stimulus.get("wav_peak_int16")
    if not isinstance(rms_manifest, dict) or not isinstance(peak_manifest, dict):
        return False
    retained = []
    if probe_required:
        retained.append((stimulus.get("ir_probe_wav"), probe_metrics))
    if background_required:
        retained.append((stimulus.get("background_wav"), background_metrics))
    expected_names = {
        Path(relative_path).name for relative_path, _metrics in retained
        if isinstance(relative_path, str)
    }
    if set(rms_manifest) != expected_names or set(peak_manifest) != expected_names:
        return False
    for relative_path, metrics in retained:
        if not isinstance(relative_path, str):
            return False
        path = root / relative_path
        name = path.name
        if abs(wav_rms_int16(path) - rms_manifest[name]) > 1.0:
            return False
        if wav_peak_int16(path) != peak_manifest[name]:
            return False
        if abs(rms_manifest[name] - metrics["final_rms_int16"]) > 1e-9:
            return False
        if peak_manifest[name] != metrics["final_peak_int16"]:
            return False
    return True


def _audio_metrics_are_valid(
    metrics: object,
    shared_gain: float,
    peak_ceiling: float,
    *,
    retained: bool,
) -> bool:
    if not isinstance(metrics, dict) or metrics.get("retained") is not retained:
        return False
    values = (
        metrics.get("raw_rms_int16"),
        metrics.get("final_rms_int16"),
        metrics.get("raw_peak_int16"),
        metrics.get("final_peak_int16"),
    )
    if not all(isinstance(value, (int, float)) for value in values):
        return False
    raw_rms, final_rms, raw_peak, final_peak = values
    if raw_rms <= 0 or final_rms <= 0 or raw_peak <= 0 or final_peak <= 0:
        return False
    if final_peak > peak_ceiling:
        return False
    if abs(final_rms - raw_rms * shared_gain) > 1.0:
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("train", "test"), default="train")
    parser.add_argument(
        "--signal", "--signal-mode", dest="signal_mode",
        choices=("visual", "ir", "mixed", "paired"), default="mixed",
    )
    parser.add_argument("--stimuli", type=int, default=12)
    parser.add_argument(
        "--glyph-composition", choices=("automatic", "1", "2", "3"),
        default="automatic",
    )
    parser.add_argument(
        "--progression", choices=("growing", "mixed"), default="mixed",
    )
    parser.add_argument(
        "--feedback", action=argparse.BooleanOptionalAction, default=False,
    )
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "advanced_sessions")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    path, _manifest = prepare_session(
        {
            "split": arguments.split,
            "signalMode": arguments.signal_mode,
            "baseStimulusCount": arguments.stimuli,
            "glyphComposition": arguments.glyph_composition,
            "progression": arguments.progression,
            "feedbackEnabled": arguments.feedback,
            "seed": arguments.seed,
        },
        arguments.out,
    )
    print(path)
