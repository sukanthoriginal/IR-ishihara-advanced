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

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.plate import (
    ALIGNED_DISPLACEMENT_AUDIO_PIXELS,
    ALIGNED_VISUAL_CARRIER_VERSION,
    ALIGNED_VISUAL_CARRIER_DOT_COUNT,
    ALIGNED_VISUAL_CARRIER_OCCUPIED_PIXEL_COUNT,
    ALIGNED_VISUAL_CARRIER_RADIUS_HISTOGRAM,
    ALIGNED_VISUAL_DENSITY_EQUIVALENCE_VERSION,
    ALIGNED_VISUAL_DOT_STEP,
    ALIGNED_VISUAL_COPY_COLOUR,
    ALIGNED_VISUAL_PALETTE_VERSION,
    ALIGNED_VISUAL_PAIR_AXIS,
    ALIGNED_VISUAL_PAIR_OFFSET_PIXELS,
    ALIGNED_VISUAL_SUBDOT_RADII,
    AUDIO_HEIGHT,
    AUDIO_WIDTH,
    GEOMETRY_SEGMENTS,
    PLATE_HEIGHT,
    PLATE_WIDTH,
    SOURCE_COLOURS,
    difference_mask,
    draw_geometry_mask,
    mask_digest,
    render_trial_images,
    segment_closure_relations,
    translate_mask_without_clipping,
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

SCHEMA_VERSION = 11
RENDER_VERSION = 9
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
ALIGNED_MIXED_CONDITIONS = (
    "visual_background_audio",
    "visual_aligned_overlay",
    "visual_aligned_ir_audio",
    "ir_audio",
)
ALIGNED_IDENTITY_CONDITIONS = (
    "visual_aligned_overlay",
    "visual_aligned_ir_audio",
)
ALIGNED_COMPLEMENTARY_CONDITIONS = (
    "visual_background_audio",
    "ir_audio",
)
VISUAL_ALIGNED_SILENT_CONDITION = "visual_aligned_silent"
VISUAL_COMPLEMENTARY_SILENT_CONDITION = "visual_complementary_silent"
VISUAL_COMPOSITE_CONDITIONS = (
    VISUAL_COMPLEMENTARY_SILENT_CONDITION,
    VISUAL_ALIGNED_SILENT_CONDITION,
)
DEFAULT_ALIGNED_MIXED_RATIO = (1, 1, 1, 2)
PROGRESSION_MODES = ("growing", "glyph-growing", "mixed")


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
    if signal_mode not in {
        "visual", "visual-aligned", "ir", "mixed", "mixed-aligned", "paired",
    }:
        raise ValueError(
            "signalMode must be visual, visual-aligned, ir, mixed, "
            "mixed-aligned, or paired"
        )

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
        base_count_value = 30
    base_count = _coerce_integer(
        base_count_value, "baseStimulusCount",
    )
    if not 4 <= base_count <= 96:
        raise ValueError("baseStimulusCount must be between 4 and 96")

    glyph_composition = str(settings.get("glyphComposition", "automatic"))
    if glyph_composition not in {"automatic", "1", "2", "3"}:
        raise ValueError("glyphComposition must be automatic, 1, 2, or 3")

    progression = settings.get("progression", "growing")
    if progression not in PROGRESSION_MODES:
        raise ValueError(
            "progression must be growing, glyph-growing, or mixed"
        )

    feedback_enabled = settings.get("feedbackEnabled", True)
    if not isinstance(feedback_enabled, bool):
        raise ValueError("feedbackEnabled must be a boolean")

    seed = _coerce_integer(settings.get("seed", 1729), "seed")
    if not 0 <= seed <= 0xFFFFFFFF:
        raise ValueError("seed must be an unsigned 32-bit integer")

    normalized = {
        "split": split,
        "signalMode": signal_mode,
        "baseStimulusCount": base_count,
        "glyphComposition": glyph_composition,
        "progression": progression,
        "feedbackEnabled": feedback_enabled,
        "seed": seed,
        "schemaVersion": SCHEMA_VERSION,
    }
    if signal_mode == "mixed-aligned":
        weights = normalize_aligned_mixed_ratio(
            settings.get("mixedConditionRatio", DEFAULT_ALIGNED_MIXED_RATIO),
        )
        normalized["mixedConditionRatio"] = ":".join(map(str, weights))
        normalized["mixedConditionWeights"] = list(weights)
    return normalized


def normalize_aligned_mixed_ratio(value: object) -> tuple[int, int, int, int]:
    if isinstance(value, str):
        pieces = value.strip().split(":")
    elif isinstance(value, (list, tuple)):
        pieces = list(value)
    else:
        raise ValueError("mixedConditionRatio must contain four positive integers")
    if len(pieces) != 4:
        raise ValueError("mixedConditionRatio must contain four positive integers")
    weights = tuple(
        _coerce_integer(piece, "mixedConditionRatio") for piece in pieces
    )
    if any(weight <= 0 for weight in weights):
        raise ValueError("mixedConditionRatio weights must be positive")
    if sum(weights) > 40:
        raise ValueError("mixedConditionRatio weights must sum to at most 40")
    divisor = math.gcd(*weights)
    return tuple(weight // divisor for weight in weights)


def weighted_condition_quotas(
    count: int,
    weights: tuple[int, ...],
    seed: int,
) -> tuple[int, ...]:
    """Allocate exact counts with seeded largest-remainder tie-breaking."""
    total_weight = sum(weights)
    base_counts = [count * weight // total_weight for weight in weights]
    remaining = count - sum(base_counts)
    tie_start = seed % len(weights)
    ranked = sorted(
        range(len(weights)),
        key=lambda index: (
            -(count * weights[index] % total_weight),
            (index - tie_start) % len(weights),
        ),
    )
    for index in ranked[:remaining]:
        base_counts[index] += 1
    return tuple(base_counts)


def condition_glyph_quota_matrix(
    condition_quotas: tuple[int, ...],
    glyph_quotas: dict[int, int],
    seed: int,
) -> dict[str, dict[int, int]]:
    """Apportion an exact condition × glyph table with both margins fixed."""
    total = sum(condition_quotas)
    if total != sum(glyph_quotas.values()) or total <= 0:
        raise ValueError("condition and glyph quotas must have the same positive total")
    if len(condition_quotas) != len(ALIGNED_MIXED_CONDITIONS):
        raise ValueError("aligned mixed mode requires four condition quotas")
    glyphs = (1, 2, 3)
    base = [
        [condition_quotas[row] * glyph_quotas[glyph] // total for glyph in glyphs]
        for row in range(len(condition_quotas))
    ]
    row_needs = [
        condition_quotas[row] - sum(base[row])
        for row in range(len(condition_quotas))
    ]
    col_needs = [
        glyph_quotas[glyph] - sum(base[row][column] for row in range(len(base)))
        for column, glyph in enumerate(glyphs)
    ]
    cells = [
        (row, column)
        for row in range(len(condition_quotas))
        for column in range(len(glyphs))
    ]
    additions = sum(row_needs)
    tie_rng = random.Random(derive_seed(seed, "condition-glyph-apportionment-v1"))
    tie_order = list(cells)
    tie_rng.shuffle(tie_order)
    tie_rank = {cell: index for index, cell in enumerate(tie_order)}
    best = None
    for selected in itertools.combinations(cells, additions):
        if any(
            sum(row == candidate_row for row, _column in selected) != need
            for candidate_row, need in enumerate(row_needs)
        ):
            continue
        if any(
            sum(column == candidate_column for _row, column in selected) != need
            for candidate_column, need in enumerate(col_needs)
        ):
            continue
        remainder_score = sum(
            condition_quotas[row] * glyph_quotas[glyphs[column]] % total
            for row, column in selected
        )
        objective = (
            -remainder_score,
            tuple(sorted(tie_rank[cell] for cell in selected)),
        )
        if best is None or objective < best[0]:
            best = (objective, selected)
    if best is None:
        raise RuntimeError("could not apportion condition × glyph quotas")
    for row, column in best[1]:
        base[row][column] += 1
    return {
        condition: {
            glyph: base[row][column]
            for column, glyph in enumerate(glyphs)
        }
        for row, condition in enumerate(ALIGNED_MIXED_CONDITIONS)
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


def eligible_transformation_counts(grammar: dict, split: str) -> dict[int, int]:
    """Count runnable transformation signatures for a source split.

    Multi-glyph signatures may use identities as context but must contain at
    least one change. A one-glyph trial additionally needs a source family
    with four distinct interpretations (identity plus three changes), matching
    :func:`select_base_trials`.
    """
    if split not in {"train", "test"}:
        raise ValueError("split must be train or test")
    families = [
        family for family in grammar["sourceFamilies"]
        if family["split"] == split
    ]
    mapping_count = sum(family["familySize"] for family in families)
    identity_count = len(families)
    return {
        1: sum(
            family["changedCount"]
            for family in families
            if family["familySize"] >= 4
        ),
        2: mapping_count ** 2 - identity_count ** 2,
        3: mapping_count ** 3 - identity_count ** 3,
    }


def mixed_aligned_eligible_counts(grammar: dict, split: str) -> dict[int, int]:
    """Count the complete split-local mapping universe for the four-way mode."""
    if split not in {"train", "test"}:
        raise ValueError("split must be train or test")
    families = [
        family for family in grammar["sourceFamilies"]
        if family["split"] == split
    ]
    mapping_count = sum(family["familySize"] for family in families)
    return {length: mapping_count ** length for length in (1, 2, 3)}


def plan_session(
    settings: dict,
    repo_root: Path = REPO_ROOT,
    grammar: dict | None = None,
) -> dict:
    """Select a deterministic session without rendering any assets.

    The local server uses this lightweight plan to audit participant-history
    repeats before committing to the expensive plate and audio render. Calling
    :func:`prepare_session` with the same settings reproduces these exact base
    specifications.
    """
    return _plan_normalized_session(
        normalize_settings(settings), grammar or load_grammar(repo_root),
    )


def _plan_normalized_session(normalized: dict, grammar: dict) -> dict:
    """Build a lightweight plan from already validated inputs."""
    selection_rng = random.Random(derive_seed(normalized["seed"], "selection-v1"))
    foil_rng = random.Random(derive_seed(normalized["seed"], "foils-v1"))
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
    condition_quotas = None
    condition_glyph_quotas = None
    combinatorial_verification = None
    if normalized["signalMode"] == "mixed-aligned":
        condition_quotas = weighted_condition_quotas(
            normalized["baseStimulusCount"],
            tuple(normalized["mixedConditionWeights"]),
            normalized["seed"],
        )
        condition_glyph_quotas = condition_glyph_quota_matrix(
            condition_quotas, quotas, normalized["seed"],
        )
        base_specs = select_mixed_aligned_trials(
            families,
            condition_glyph_quotas,
            selection_rng,
            foil_rng,
            [item["id"] for item in grammar["geometries"]],
        )
        eligible_by_length = mixed_aligned_eligible_counts(
            grammar, normalized["split"],
        )
        combinatorial_verification = verify_mixed_aligned_specifications(
            base_specs,
            grammar,
            normalized["split"],
            dict(zip(ALIGNED_MIXED_CONDITIONS, condition_quotas)),
            quotas,
            condition_glyph_quotas,
        )
    else:
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
        eligible_by_length = eligible_transformation_counts(
            grammar, normalized["split"],
        )
    enabled_lengths = (
        (1, 2, 3)
        if normalized["glyphComposition"] == "automatic"
        else (int(normalized["glyphComposition"]),)
    )
    return {
        "settings": normalized,
        "catalog_version": grammar.get("catalogVersion", 1),
        "glyph_count_quotas": quotas,
        "eligible_by_glyph_count": eligible_by_length,
        "eligible_transformation_count": sum(
            eligible_by_length[length] for length in enabled_lengths
        ),
        "condition_quotas": (
            dict(zip(ALIGNED_MIXED_CONDITIONS, condition_quotas))
            if condition_quotas is not None else None
        ),
        "condition_glyph_quotas": condition_glyph_quotas,
        "combinatorial_verification": combinatorial_verification,
        "base_specs": base_specs,
    }


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
    schedule_rng = random.Random(derive_seed(normalized["seed"], "schedule-v1"))
    planned = _plan_normalized_session(normalized, grammar)
    quotas = planned["glyph_count_quotas"]
    base_specs = planned["base_specs"]

    with tempfile.TemporaryDirectory(prefix="advanced-build-", dir=output_root) as temp_name:
        build_root = Path(temp_name)
        stimuli = []
        raspivoice_bin = default_raspivoice_bin(repo_root)
        for index, spec in enumerate(base_specs, start=1):
            stem = f"stimulus_{index:03d}"
            assigned_condition = spec.get("assignedCondition")
            assets = render_trial_images(
                spec["sourceIds"],
                spec["targetIds"],
                spec["choiceTargets"],
                build_root,
                stem,
                derive_seed(normalized["seed"], f"render-v1:{index}"),
                include_aligned_assets=(
                    normalized["signalMode"] == "visual-aligned"
                    or (
                        normalized["signalMode"] == "mixed-aligned"
                        and assigned_condition in ALIGNED_IDENTITY_CONDITIONS
                    )
                ),
                include_balanced_carrier_assets=(
                    normalized["signalMode"] == "mixed-aligned"
                ),
                include_visual_complementary_asset=(
                    normalized["signalMode"] == "visual-aligned"
                    or (
                        normalized["signalMode"] == "mixed-aligned"
                        and assigned_condition == "visual_background_audio"
                    )
                ),
            )
            target_choice = next(
                item for item in assets["choices"]
                if item["target_ids"] == spec["targetIds"]
            )
            decoy_choice = (
                None
                if spec["sourceIds"] == spec["targetIds"]
                else next(
                    item for item in assets["choices"]
                    if item["target_ids"] == spec["sourceIds"]
                )
            )

            audio_assets = {
                "ir_probe_wav": None,
                "background_wav": None,
                "wav_rms_int16": None,
                "wav_peak_int16": None,
                "audio_normalization": None,
            }
            if normalized["signalMode"] == "mixed-aligned":
                audio_assets["aligned_target_wav"] = None
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
                "decoy_choice_id": (
                    decoy_choice["choice_id"] if decoy_choice is not None else None
                ),
                "response_choices": assets["choices"],
                **audio_assets,
                "transformation_signature": spec["transformationSignature"],
                "mapping_repetition_index": spec["mappingRepetitionIndex"],
                "mapping_class": spec.get(
                    "mappingClass",
                    "identity" if spec["changedCount"] == 0 else "changed",
                ),
                "choice_rule": spec.get("choiceRule", "same-family-v1"),
                **(
                    {"assigned_condition": assigned_condition}
                    if assigned_condition is not None else {}
                ),
                "estimated_difficulty_score": difficulty["score"],
                "difficulty_components": difficulty["components"],
                "difficulty_model_version": DIFFICULTY_MODEL_VERSION,
                "difficulty_inputs": difficulty["inputs"],
                **(
                    {
                        "visible_signal_dot_count": assets[
                            "aligned_visual_base_dot_count"
                        ]
                    }
                    if normalized["signalMode"] == "visual-aligned" else {}
                ),
                **{key: value for key, value in assets.items() if key != "choices"},
            })

        rendered_combinatorial_verification = planned.get(
            "combinatorial_verification"
        )
        if normalized["signalMode"] == "mixed-aligned":
            for spec, stimulus in zip(base_specs, stimuli):
                if spec["mappingClass"] == "identity":
                    spec["_renderedSignalDotCount"] = stimulus[
                        "canonical_visual_dot_count"
                    ]
            _balance_identity_signal_load(
                base_specs,
                planned["condition_glyph_quotas"],
                random.Random(derive_seed(normalized["seed"], "rendered-density-v1")),
            )
            for spec, stimulus in zip(base_specs, stimuli):
                stimulus["assigned_condition"] = spec["assignedCondition"]
                stimulus["visible_signal_dot_count"] = stimulus[
                    "balanced_visual_source_dot_count"
                ]
            rendered_combinatorial_verification = (
                verify_mixed_aligned_specifications(
                    base_specs,
                    grammar,
                    normalized["split"],
                    planned["condition_quotas"],
                    quotas,
                    planned["condition_glyph_quotas"],
                )
            )

        assign_difficulty_ranks(stimuli)
        mixed_balance = None
        if normalized["signalMode"] == "mixed":
            assign_mixed_conditions(stimuli, normalized["seed"])
            generate_mixed_audio_assets(
                stimuli, build_root, raspivoice_bin,
            )
            mixed_balance = summarize_mixed_condition_balance(stimuli)
        elif normalized["signalMode"] == "mixed-aligned":
            generate_aligned_mixed_audio_assets(
                stimuli, build_root, raspivoice_bin,
            )
            mixed_balance = summarize_condition_balance(
                stimuli, ALIGNED_MIXED_CONDITIONS,
            )
            mixed_balance["visible_signal_density_balance"] = (
                summarize_visible_signal_density(
                    stimuli, ALIGNED_MIXED_CONDITIONS,
                )
            )
        elif normalized["signalMode"] == "visual-aligned":
            assign_mixed_conditions(
                stimuli,
                normalized["seed"],
                conditions=VISUAL_COMPOSITE_CONDITIONS,
                match_prefix="visual-composite-match",
            )
            mixed_balance = summarize_mixed_condition_balance(
                stimuli, VISUAL_COMPOSITE_CONDITIONS,
            )
            mixed_balance["visible_signal_density_balance"] = (
                summarize_visible_signal_density(
                    stimuli, VISUAL_COMPOSITE_CONDITIONS,
                )
            )
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
                    "visual_silent", *VISUAL_COMPOSITE_CONDITIONS,
                    "visual_background_audio",
                    "visual_aligned_overlay", "visual_aligned_ir_audio",
                    "ir_audio",
                )
                if any(trial["condition"] == condition for trial in trials)
            },
            "comparison_design": (
                "grammar-stratified-aligned-identities-vs-visual-and-ir-complements"
                if normalized["signalMode"] == "mixed-aligned"
                else "distinct-stimulus-carrier-controlled-between-condition"
                if normalized["signalMode"] == "mixed"
                else "within-stimulus-repeated"
                if normalized["signalMode"] == "paired"
                else "distinct-stimulus-silent-aligned-visual-control"
                if normalized["signalMode"] == "visual-aligned"
                else "single-condition"
            ),
            "stimuli_repeated_across_conditions": normalized["signalMode"] == "paired",
            "condition_assignment": (
                {
                    "method": (
                        "exact-condition-glyph-density-apportionment-v4"
                        if normalized["signalMode"] == "mixed-aligned"
                        else "provisional-structural-matching-v1"
                    ),
                    "matching_priority": (
                        "exact condition and glyph margins; identity/change grammar class"
                        if normalized["signalMode"] == "mixed-aligned"
                        else "glyph count, difficulty stratum, changed count, score"
                    ),
                    **(
                        {
                            "condition_ratio": normalized["mixedConditionRatio"],
                            "aligned_displacement_audio_pixels": (
                                ALIGNED_DISPLACEMENT_AUDIO_PIXELS
                            ),
                        }
                        if normalized["signalMode"] == "mixed-aligned"
                        else {
                            "pair_orientation": (
                                "seeded alternating orientation; boundary pairs "
                                "optimized for stratum balance"
                            ),
                            "odd_remainder": (
                                "visual_complementary_silent when seed is even; "
                                "visual_aligned_silent when seed is odd"
                                if normalized["signalMode"] == "visual-aligned"
                                else
                                "visual_background_audio when seed is even; "
                                "ir_audio when seed is odd"
                            ),
                        }
                    ),
                    "audio_matching": (
                        "not applicable; both visual controls are silent"
                        if normalized["signalMode"] == "visual-aligned"
                        else
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
            "combinatorial_verification": rendered_combinatorial_verification,
            "audio_generated": normalized["signalMode"] in {
                "ir", "mixed", "mixed-aligned", "paired",
            },
            "audio_normalization_method": (
                AUDIO_NORMALIZATION_METHOD
                if normalized["signalMode"] in {
                    "ir", "mixed", "mixed-aligned", "paired",
                }
                else None
            ),
            "audio_carrier_target_rms_int16": (
                CARRIER_TARGET_RMS_INT16
                if normalized["signalMode"] in {
                    "ir", "mixed", "mixed-aligned", "paired",
                }
                else None
            ),
            "audio_peak_ceiling_dbfs": (
                PEAK_CEILING_DBFS
                if normalized["signalMode"] in {
                    "ir", "mixed", "mixed-aligned", "paired",
                }
                else None
            ),
            "audio_counterfactual_shared_gain": (
                normalized["signalMode"] in {
                    "ir", "mixed", "mixed-aligned", "paired",
                }
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
    record = {
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
    return record


def select_mixed_aligned_trials(
    families: list[dict],
    condition_glyph_quotas: dict[str, dict[int, int]],
    rng: random.Random,
    foil_rng: random.Random,
    geometry_ids: list[str],
) -> list[dict]:
    """Select identity controls and changed complements from one grammar.

    The aligned visual and aligned IR conditions consume identity combinations.
    The visual-complementary and IR-complementary conditions consume only
    combinations containing at least one valid grammar change.
    Selection is without replacement until the relevant finite catalog is
    exhausted, after which repetition is explicit in ``mappingRepetitionIndex``.
    """
    if not families:
        raise ValueError("source split has no families")
    family_by_source = {family["sourceId"]: family for family in families}
    identity_catalogs = {}
    identity_cursors = {length: 0 for length in (1, 2, 3)}
    changed_one_catalog = [
        (family, target_id)
        for family in families
        for target_id in family["changedTargetIds"]
    ]
    rng.shuffle(changed_one_catalog)
    changed_one_cursor = 0
    used_changed = {length: set() for length in (1, 2, 3)}
    repetition_counts: dict[str, int] = {}
    selected = []

    for length in (1, 2, 3):
        catalog = list(itertools.product(families, repeat=length))
        rng.shuffle(catalog)
        identity_catalogs[length] = catalog

    condition_slots = []
    for length in (1, 2, 3):
        slots = [
            condition
            for condition in ALIGNED_MIXED_CONDITIONS
            for _index in range(condition_glyph_quotas[condition][length])
        ]
        rng.shuffle(slots)
        condition_slots.extend((condition, length) for condition in slots)

    for condition, length in condition_slots:
        if condition in ALIGNED_IDENTITY_CONDITIONS:
            catalog = identity_catalogs[length]
            chosen_tuple = catalog[identity_cursors[length] % len(catalog)]
            identity_cursors[length] += 1
            chosen = list(chosen_tuple)
            source_ids = [family["sourceId"] for family in chosen]
            target_ids = list(source_ids)
            mapping_class = "identity"
        else:
            mapping_class = "changed"
            if length == 1:
                family, target_id = changed_one_catalog[
                    changed_one_cursor % len(changed_one_catalog)
                ]
                changed_one_cursor += 1
                chosen = [family]
                source_ids = [family["sourceId"]]
                target_ids = [target_id]
            else:
                candidate = None
                duplicate = None
                for _attempt in range(500):
                    chosen_attempt = [rng.choice(families) for _ in range(length)]
                    sources_attempt = [
                        family["sourceId"] for family in chosen_attempt
                    ]
                    targets_attempt = [
                        rng.choice([
                            family["sourceId"], *family["changedTargetIds"],
                        ])
                        for family in chosen_attempt
                    ]
                    if targets_attempt == sources_attempt:
                        position = rng.randrange(length)
                        targets_attempt[position] = rng.choice(
                            chosen_attempt[position]["changedTargetIds"]
                        )
                    signature = transformation_signature(
                        sources_attempt, targets_attempt,
                    )
                    built = (chosen_attempt, sources_attempt, targets_attempt)
                    if signature not in used_changed[length]:
                        candidate = built
                        break
                    if duplicate is None:
                        duplicate = built
                chosen, source_ids, target_ids = candidate or duplicate or (None, None, None)
                if chosen is None:
                    raise RuntimeError("could not construct a changed aligned-mixed trial")

        choice_targets = choose_mixed_aligned_interpretations(
            chosen, target_ids, foil_rng, geometry_ids,
        )
        signature = transformation_signature(source_ids, target_ids)
        repetition_counts[signature] = repetition_counts.get(signature, 0) + 1
        if mapping_class == "changed":
            used_changed[length].add(signature)
        record = _base_trial_record(
            chosen,
            source_ids,
            target_ids,
            choice_targets,
            signature,
            repetition_counts[signature],
        )
        record.update({
            "assignedCondition": condition,
            "mappingClass": mapping_class,
            "choiceRule": "family-first-canonical-geometry-foils-v1",
        })
        selected.append(record)

    _balance_identity_signal_load(selected, condition_glyph_quotas, rng)
    rng.shuffle(selected)
    return selected


def _identity_signal_pixel_count(spec: dict) -> int:
    """Return rendered signal dots when known, otherwise canonical mask pixels."""
    rendered_count = spec.get("_renderedSignalDotCount")
    if rendered_count is not None:
        if not isinstance(rendered_count, int) or rendered_count < 0:
            raise RuntimeError("rendered identity signal-dot count is invalid")
        return rendered_count
    mask = draw_geometry_mask(spec["targetIds"])
    return sum(pixel > 0 for pixel in mask.tobytes())


def _identity_load_objective(
    loads: dict[str, int],
    quotas: dict[str, int],
) -> float:
    """Squared spread of per-stimulus signal load across non-empty conditions."""
    means = [
        loads[condition] / quota
        for condition, quota in quotas.items()
        if quota > 0
    ]
    if not means:
        return 0.0
    centre = sum(means) / len(means)
    return sum((mean - centre) ** 2 for mean in means)


def _balance_identity_signal_load(
    specs: list[dict],
    condition_glyph_quotas: dict[str, dict[int, int]],
    rng: random.Random,
) -> None:
    """Assign identity labels while preserving margins and balancing mask load.

    The chosen identity combinations remain unchanged. Only their two
    identity-condition labels are reassigned within glyph count. A largest-first
    constrained pass is followed by improving pair swaps, so no legal
    same-glyph swap can further reduce the per-condition raster-load spread.
    """
    for glyph_count in (1, 2, 3):
        candidates = [
            spec for spec in specs
            if spec["mappingClass"] == "identity"
            and len(spec["sourceIds"]) == glyph_count
        ]
        quotas = {
            condition: condition_glyph_quotas[condition][glyph_count]
            for condition in ALIGNED_IDENTITY_CONDITIONS
        }
        if len(candidates) != sum(quotas.values()):
            raise RuntimeError("identity density-balancing margins are inconsistent")
        loads = {condition: 0 for condition in ALIGNED_IDENTITY_CONDITIONS}
        remaining = dict(quotas)
        randomized = [(rng.random(), spec) for spec in candidates]
        randomized.sort(
            key=lambda item: (-_identity_signal_pixel_count(item[1]), item[0])
        )
        for _tie_breaker, spec in randomized:
            load = _identity_signal_pixel_count(spec)
            eligible = [
                condition for condition in ALIGNED_IDENTITY_CONDITIONS
                if remaining[condition] > 0
            ]
            condition = min(
                eligible,
                key=lambda item: (
                    (loads[item] + load) / quotas[item],
                    loads[item] / quotas[item],
                    item,
                ),
            )
            spec["assignedCondition"] = condition
            loads[condition] += load
            remaining[condition] -= 1

        while True:
            current = _identity_load_objective(loads, quotas)
            best_improvement = 0.0
            best_swap = None
            for left_index, left in enumerate(candidates):
                left_condition = left["assignedCondition"]
                left_load = _identity_signal_pixel_count(left)
                for right in candidates[left_index + 1:]:
                    right_condition = right["assignedCondition"]
                    if left_condition == right_condition:
                        continue
                    right_load = _identity_signal_pixel_count(right)
                    trial_loads = dict(loads)
                    trial_loads[left_condition] += right_load - left_load
                    trial_loads[right_condition] += left_load - right_load
                    improvement = current - _identity_load_objective(
                        trial_loads, quotas,
                    )
                    if improvement > best_improvement + 1e-9:
                        best_improvement = improvement
                        best_swap = (
                            left, right, left_condition, right_condition,
                            left_load, right_load,
                        )
            if best_swap is None:
                break
            left, right, left_condition, right_condition, left_load, right_load = (
                best_swap
            )
            left["assignedCondition"] = right_condition
            right["assignedCondition"] = left_condition
            loads[left_condition] += right_load - left_load
            loads[right_condition] += left_load - right_load


def _verify_identity_signal_balance(
    specs: list[dict],
    condition_glyph_quotas: dict[str, dict[int, int]],
) -> dict:
    """Reject an identity allocation if a legal pair swap can improve it."""
    by_glyph = {}
    total_loads = {condition: 0 for condition in ALIGNED_IDENTITY_CONDITIONS}
    total_counts = {condition: 0 for condition in ALIGNED_IDENTITY_CONDITIONS}
    for glyph_count in (1, 2, 3):
        candidates = [
            spec for spec in specs
            if spec["mappingClass"] == "identity"
            and len(spec["sourceIds"]) == glyph_count
        ]
        quotas = {
            condition: condition_glyph_quotas[condition][glyph_count]
            for condition in ALIGNED_IDENTITY_CONDITIONS
        }
        loads = {
            condition: sum(
                _identity_signal_pixel_count(spec)
                for spec in candidates
                if spec["assignedCondition"] == condition
            )
            for condition in ALIGNED_IDENTITY_CONDITIONS
        }
        current = _identity_load_objective(loads, quotas)
        for left_index, left in enumerate(candidates):
            left_condition = left["assignedCondition"]
            left_load = _identity_signal_pixel_count(left)
            for right in candidates[left_index + 1:]:
                right_condition = right["assignedCondition"]
                if left_condition == right_condition:
                    continue
                right_load = _identity_signal_pixel_count(right)
                trial_loads = dict(loads)
                trial_loads[left_condition] += right_load - left_load
                trial_loads[right_condition] += left_load - right_load
                if _identity_load_objective(trial_loads, quotas) < current - 1e-9:
                    raise RuntimeError(
                        "identity signal density has an improving legal pair swap"
                    )
        means = {
            condition: (
                loads[condition] / quotas[condition]
                if quotas[condition] else None
            )
            for condition in ALIGNED_IDENTITY_CONDITIONS
        }
        by_glyph[str(glyph_count)] = {
            "pixel_loads": loads,
            "mean_pixels_per_stimulus": means,
            "pair_swap_local_optimum": True,
        }
        for condition in ALIGNED_IDENTITY_CONDITIONS:
            total_loads[condition] += loads[condition]
            total_counts[condition] += quotas[condition]
    total_means = {
        condition: (
            total_loads[condition] / total_counts[condition]
            if total_counts[condition] else None
        )
        for condition in ALIGNED_IDENTITY_CONDITIONS
    }
    populated_means = [value for value in total_means.values() if value is not None]
    relative_spread = (
        (max(populated_means) - min(populated_means))
        / (sum(populated_means) / len(populated_means))
        if populated_means and sum(populated_means) else 0.0
    )
    return {
        "version": "rendered-dot-or-mask-load-pair-swap-v2",
        "load_basis": (
            "rendered_signal_dot_count"
            if any("_renderedSignalDotCount" in spec for spec in specs)
            else "canonical_mask_pixel_count"
        ),
        "verified": True,
        "by_glyph_count": by_glyph,
        "total_pixel_loads": total_loads,
        "mean_pixels_per_stimulus": total_means,
        "relative_mean_spread": relative_spread,
    }


def choose_mixed_aligned_interpretations(
    families: list[dict],
    target_ids: list[str],
    rng: random.Random,
    geometry_ids: list[str],
) -> list[list[str]]:
    """Build four unique choices for identities and small source families."""
    source_ids = [family["sourceId"] for family in families]
    target = tuple(target_ids)
    source = tuple(source_ids)
    interpretations = [target]
    if source != target:
        interpretations.append(source)
    family_outcomes = [
        [family["sourceId"], *family["changedTargetIds"]]
        for family in families
    ]
    family_candidates = [
        candidate for candidate in itertools.product(*family_outcomes)
        if candidate not in interpretations
    ]
    global_candidates = set()
    for basis in (target, source):
        for position in range(len(target)):
            for geometry_id in geometry_ids:
                candidate = list(basis)
                candidate[position] = geometry_id
                global_candidates.add(tuple(candidate))
    global_candidates.difference_update(interpretations)
    global_candidates.difference_update(family_candidates)

    target_segment_count = sum(len(GEOMETRY_SEGMENTS[item]) for item in target)

    def score(candidate: tuple[str, ...]) -> tuple[float, ...]:
        segment_count = sum(len(GEOMETRY_SEGMENTS[item]) for item in candidate)
        hamming = sum(left != right for left, right in zip(candidate, target))
        return (
            hamming,
            abs(segment_count - target_segment_count),
            -_interpretation_similarity(list(target), list(candidate)),
            rng.random(),
        )

    family_candidates.sort(key=score)
    globally_ranked = sorted(global_candidates, key=score)
    for candidate in [*family_candidates, *globally_ranked]:
        if candidate not in interpretations:
            interpretations.append(candidate)
        if len(interpretations) == 4:
            break
    if len(interpretations) != 4 or len(set(interpretations)) != 4:
        raise RuntimeError("could not construct four unique canonical interpretations")
    rng.shuffle(interpretations)
    return [list(item) for item in interpretations]


def verify_mixed_aligned_specifications(
    specs: list[dict],
    grammar: dict,
    split: str,
    condition_quotas: dict[str, int],
    glyph_quotas: dict[int, int],
    condition_glyph_quotas: dict[str, dict[int, int]],
) -> dict:
    """Fail closed unless every planned slot obeys the declared combinatorics."""
    mapping_by_id = {item["id"]: item for item in grammar["mappings"]}
    geometry_ids = {item["id"] for item in grammar["geometries"]}
    observed_conditions = {
        condition: sum(spec["assignedCondition"] == condition for spec in specs)
        for condition in ALIGNED_MIXED_CONDITIONS
    }
    if observed_conditions != condition_quotas:
        raise RuntimeError("aligned condition quotas differ from the plan")
    observed_glyphs = {
        length: sum(len(spec["sourceIds"]) == length for spec in specs)
        for length in (1, 2, 3)
    }
    if observed_glyphs != glyph_quotas:
        raise RuntimeError("aligned glyph quotas differ from the plan")
    observed_matrix = {
        condition: {
            length: sum(
                spec["assignedCondition"] == condition
                and len(spec["sourceIds"]) == length
                for spec in specs
            )
            for length in (1, 2, 3)
        }
        for condition in ALIGNED_MIXED_CONDITIONS
    }
    if observed_matrix != condition_glyph_quotas:
        raise RuntimeError("aligned condition × glyph quotas differ from the plan")
    identity_density_balance = _verify_identity_signal_balance(
        specs, condition_glyph_quotas,
    )

    for spec in specs:
        condition = spec["assignedCondition"]
        is_identity_condition = condition in ALIGNED_IDENTITY_CONDITIONS
        mappings = []
        for source_id, target_id, mapping_id in zip(
            spec["sourceIds"], spec["targetIds"], spec["mappingIds"],
        ):
            mapping = mapping_by_id.get(mapping_id)
            if mapping is None:
                raise RuntimeError(f"unknown planned mapping: {mapping_id}")
            if (
                mapping["sourceId"] != source_id
                or mapping["targetId"] != target_id
                or mapping["sourceSplit"] != split
            ):
                raise RuntimeError("planned mapping differs from canonical grammar")
            mappings.append(mapping)
        changed_count = sum(mapping["changed"] for mapping in mappings)
        if changed_count != spec["changedCount"]:
            raise RuntimeError("planned changed count is inconsistent")
        if is_identity_condition:
            if changed_count != 0 or spec["sourceIds"] != spec["targetIds"]:
                raise RuntimeError("aligned controls must use only identity mappings")
        elif changed_count < 1:
            raise RuntimeError("complementary trials must contain a grammar change")
        choices = [tuple(choice) for choice in spec["choiceTargets"]]
        target = tuple(spec["targetIds"])
        if len(choices) != 4 or len(set(choices)) != 4:
            raise RuntimeError("aligned trials require four unique interpretations")
        if choices.count(target) != 1:
            raise RuntimeError("aligned target must occur exactly once in choices")
        if not is_identity_condition and choices.count(tuple(spec["sourceIds"])) != 1:
            raise RuntimeError("complementary source decoy must occur exactly once")
        if any(any(item not in geometry_ids for item in choice) for choice in choices):
            raise RuntimeError("aligned choice uses an unknown geometry")

    families = [
        family for family in grammar["sourceFamilies"]
        if family["split"] == split
    ]
    identity_count = len(families)
    mapping_count = sum(family["familySize"] for family in families)
    eligible = {
        str(length): {
            "identity": identity_count ** length,
            "changed": mapping_count ** length - identity_count ** length,
            "total": mapping_count ** length,
        }
        for length in (1, 2, 3)
    }
    return {
        "verified": True,
        "version": "split-local-identity-change-density-laws-v3",
        "split": split,
        "source_family_count": identity_count,
        "mapping_count": mapping_count,
        "eligible_by_glyph_count": eligible,
        "condition_counts": observed_conditions,
        "glyph_counts": {str(key): value for key, value in observed_glyphs.items()},
        "condition_by_glyph_count": {
            condition: {str(key): value for key, value in counts.items()}
            for condition, counts in observed_matrix.items()
        },
        "identity_condition_count": sum(
            observed_conditions[condition]
            for condition in ALIGNED_IDENTITY_CONDITIONS
        ),
        "changed_condition_count": sum(
            observed_conditions[condition]
            for condition in ALIGNED_COMPLEMENTARY_CONDITIONS
        ),
        "condition_mapping_classes": {
            condition: (
                "identity"
                if condition in ALIGNED_IDENTITY_CONDITIONS else "changed"
            )
            for condition in ALIGNED_MIXED_CONDITIONS
        },
        "identity_density_balance": identity_density_balance,
        "laws": {
            "identity": "source_id == target_id at every position",
            "changed": "at least one canonical addition-only mapping",
            "visual_complementary": (
                "RGB source plus yellow diagnostic additions equals target"
            ),
            "total": "mapping_count ** glyph_count",
            "split": "every source family belongs to the selected split",
            "identity_density": (
                "canonical mask-pixel load is a same-glyph pair-swap local optimum"
            ),
        },
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


def assign_mixed_conditions(
    stimuli: list[dict],
    seed: int,
    conditions: tuple[str, str] = ("visual_background_audio", "ir_audio"),
    match_prefix: str = "mixed-match",
) -> None:
    """Match distinct puzzles structurally, then assign opposite conditions."""
    if len(set(conditions)) != 2 or not all(conditions):
        raise ValueError("mixed assignment requires two distinct condition names")
    first_condition, second_condition = conditions
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
    remainder_condition = first_condition if seed % 2 == 0 else second_condition

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
        for pair_index, first_condition_first in enumerate(orientations):
            pair_conditions = (
                (first_condition, second_condition)
                if first_condition_first else (second_condition, first_condition)
            )
            assigned.extend(zip(matched_pairs[pair_index], pair_conditions))
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
                stratum_conditions.count(first_condition)
                - stratum_conditions.count(second_condition)
            ))
        glyph_differences = []
        for glyph_count in sorted(by_glyph_count):
            glyph_conditions = [
                condition
                for stimulus, condition in assigned
                if (len(stimulus.get("source_ids", ())) or 1) == glyph_count
            ]
            glyph_differences.append(abs(
                glyph_conditions.count(first_condition)
                - glyph_conditions.count(second_condition)
            ))
        first_scores = [
            stimulus.get("estimated_difficulty_score", 0)
            for stimulus, condition in assigned
            if condition == first_condition
        ]
        second_scores = [
            stimulus.get("estimated_difficulty_score", 0)
            for stimulus, condition in assigned
            if condition == second_condition
        ]
        score_mean_gap = abs(
            sum(first_scores) / len(first_scores)
            - sum(second_scores) / len(second_scores)
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
        first_condition_first = best_orientations[pair_index]
        pair_conditions = (
            (first_condition, second_condition)
            if first_condition_first else (second_condition, first_condition)
        )
        match_id = f"{match_prefix}-{pair_index + 1:03d}"
        score_gap = round(abs(
            first.get("estimated_difficulty_score", 0)
            - second.get("estimated_difficulty_score", 0)
        ), 4)
        for position, (stimulus, condition) in enumerate(
            zip((first, second), pair_conditions), start=1,
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


def generate_aligned_target_audio_assets(
    assets: dict,
    build_root: Path,
    stem: str,
    raspivoice_bin: Path,
) -> dict:
    """Render a shifted full-target probe and its carrier reference."""
    audio_dir = build_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    aligned_path = audio_dir / f"{stem}_aligned_target.wav"
    background_path = audio_dir / f"{stem}_background.wav"
    generate_soundscape(
        build_root / assets["aligned_input_png"],
        aligned_path,
        raspivoice_bin,
        build_root,
    )
    generate_soundscape(
        build_root / assets["background_input_png"],
        background_path,
        raspivoice_bin,
        build_root,
    )
    normalization = apply_carrier_referenced_gain(
        (aligned_path, background_path),
        background_path,
    )
    file_metrics = normalization.pop("files")
    normalization["counterfactuals"] = {
        "aligned_target": {
            **file_metrics[aligned_path.name],
            "retained": True,
        },
        "background_carrier": {
            **file_metrics[background_path.name],
            "retained": False,
        },
    }
    background_path.unlink()
    return {
        "ir_probe_wav": None,
        "aligned_target_wav": str(aligned_path.relative_to(build_root)),
        "background_wav": None,
        "wav_rms_int16": {
            aligned_path.name: file_metrics[aligned_path.name]["final_rms_int16"],
        },
        "wav_peak_int16": {
            aligned_path.name: file_metrics[aligned_path.name]["final_peak_int16"],
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


def generate_aligned_mixed_audio_assets(
    stimuli: list[dict],
    build_root: Path,
    raspivoice_bin: Path,
) -> None:
    for stimulus in stimuli:
        stem = stimulus["stimulus_id"]
        condition = stimulus["assigned_condition"]
        if condition in {"visual_background_audio", "visual_aligned_overlay"}:
            audio_assets = generate_counterfactual_audio_assets(
                stimulus,
                build_root,
                stem,
                raspivoice_bin,
                retain_probe=False,
                retain_background=True,
            )
        elif condition == "visual_aligned_ir_audio":
            audio_assets = generate_aligned_target_audio_assets(
                stimulus, build_root, stem, raspivoice_bin,
            )
        elif condition == "ir_audio":
            audio_assets = generate_counterfactual_audio_assets(
                stimulus,
                build_root,
                stem,
                raspivoice_bin,
                retain_probe=True,
                retain_background=False,
            )
        else:
            raise ValueError(f"unknown aligned mixed condition: {condition}")
        stimulus.update(audio_assets)


def summarize_mixed_condition_balance(
    stimuli: list[dict],
    conditions: tuple[str, str] = ("visual_background_audio", "ir_audio"),
) -> dict:

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


def summarize_condition_balance(
    stimuli: list[dict],
    conditions: tuple[str, ...],
) -> dict:
    def counts(items: list[dict]) -> dict[str, int]:
        return {
            condition: sum(item["assigned_condition"] == condition for item in items)
            for condition in conditions
        }

    condition_counts = counts(stimuli)
    by_stratum = {
        stratum: counts([
            item for item in stimuli if item["difficulty_stratum"] == stratum
        ])
        for stratum in ("easy", "moderate", "hard")
    }
    by_glyph_count = {
        str(glyph_count): counts([
            item for item in stimuli
            if len(item.get("source_ids", ())) == glyph_count
        ])
        for glyph_count in (1, 2, 3)
    }
    score_means = {}
    for condition in conditions:
        scores = [
            item["estimated_difficulty_score"] for item in stimuli
            if item["assigned_condition"] == condition
        ]
        score_means[condition] = round(sum(scores) / len(scores), 4) if scores else None
    return {
        "condition_counts": condition_counts,
        "by_difficulty_stratum": by_stratum,
        "by_glyph_count": by_glyph_count,
        "estimated_difficulty_score_means": score_means,
    }


def summarize_visible_signal_density(
    stimuli: list[dict],
    conditions: tuple[str, ...],
) -> dict:
    """Audit coloured source-token load without confusing it with carrier load."""
    def summary(items: list[dict]) -> dict:
        loads = [item["visible_signal_dot_count"] for item in items]
        return {
            "count": len(loads),
            "total_signal_dots": sum(loads),
            "mean_signal_dots": (
                round(sum(loads) / len(loads), 6) if loads else None
            ),
            "minimum_signal_dots": min(loads) if loads else None,
            "maximum_signal_dots": max(loads) if loads else None,
        }

    overall = {
        condition: summary([
            item for item in stimuli
            if item["assigned_condition"] == condition
        ])
        for condition in conditions
    }
    by_glyph = {
        str(glyph_count): {
            condition: summary([
                item for item in stimuli
                if item["assigned_condition"] == condition
                and len(item.get("source_ids", ())) == glyph_count
            ])
            for condition in conditions
        }
        for glyph_count in (1, 2, 3)
    }
    means = [
        values["mean_signal_dots"]
        for values in overall.values()
        if values["mean_signal_dots"] is not None
    ]
    return {
        "version": "visible-source-token-audit-v1",
        "carrier_density_is_fixed_separately": True,
        "by_condition": overall,
        "by_condition_and_glyph_count": by_glyph,
        "relative_condition_mean_spread": (
            round(
                (max(means) - min(means)) / (sum(means) / len(means)),
                8,
            )
            if means and sum(means) else 0.0
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
    progression = settings.get("progression", "growing")
    ordered = list(stimuli)
    if progression == "growing":
        ordered.sort(key=lambda item: (
            item.get("estimated_difficulty_score", 0),
            item.get("transformation_signature", item["stimulus_id"]),
            item["stimulus_id"],
        ))
    elif progression == "glyph-growing":
        ordered = glyph_staircase_order(ordered, rng)
    elif progression == "mixed":
        rng.shuffle(ordered)
    else:
        raise ValueError(
            "progression must be growing, glyph-growing, or mixed"
        )

    if signal_mode == "visual-aligned":
        if any("assigned_condition" not in stimulus for stimulus in stimuli):
            raise ValueError(
                "visual-aligned conditions must be structurally assigned"
            )
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
    if signal_mode in {"visual", "ir"}:
        condition = {
            "visual": "visual_silent",
            "ir": "ir_audio",
        }[signal_mode]
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
    if signal_mode == "mixed-aligned":
        if any("assigned_condition" not in stimulus for stimulus in stimuli):
            raise ValueError(
                "mixed-aligned conditions must be assigned by the grammar planner"
            )
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
        raise ValueError(
            "signalMode must be visual, visual-aligned, ir, mixed, "
            "mixed-aligned, or paired"
        )

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
        if progression in {"growing", "glyph-growing"}
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


def glyph_staircase_order(
    stimuli: list[dict], rng: random.Random,
) -> list[dict]:
    """Grow only glyph load while shuffling all stimulus natures per tier.

    Conditions are already assigned before scheduling. Uniformly shuffling the
    complete stimulus records inside each glyph-count tier therefore shuffles
    identity/aligned/complementary condition nature without altering any exact
    condition-by-glyph margin established by the planner.
    """
    tiers = {glyph_count: [] for glyph_count in (1, 2, 3)}
    for stimulus in stimuli:
        source_ids = stimulus.get("source_ids")
        if not isinstance(source_ids, list) or len(source_ids) not in tiers:
            raise ValueError(
                "glyph-growing progression requires one to three source_ids "
                "per stimulus"
            )
        tiers[len(source_ids)].append(stimulus)

    ordered = []
    for glyph_count in (1, 2, 3):
        tier = tiers[glyph_count]
        rng.shuffle(tier)
        ordered.extend(tier)
    return ordered


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
        plate = stimulus.get(
            "balanced_carrier_ir_plate_png", stimulus["ir_plate_png"],
        )
        wav = stimulus["ir_probe_wav"]
        audio_content = "diagnostic-ir-probe-plus-background-carrier"
    elif condition == "visual_background_audio":
        plate = stimulus.get(
            "visual_complementary_plate_png",
            stimulus.get("canonical_visual_plate_png", stimulus["visual_plate_png"]),
        )
        wav = stimulus["background_wav"]
        audio_content = "background-only-carrier"
    elif condition == "visual_aligned_overlay":
        plate = stimulus["visual_aligned_plate_png"]
        wav = stimulus["background_wav"]
        audio_content = "background-only-carrier"
    elif condition == "visual_aligned_ir_audio":
        plate = stimulus["canonical_visual_plate_png"]
        wav = stimulus["aligned_target_wav"]
        audio_content = "shifted-full-target-ir-plus-background-carrier"
    elif condition == VISUAL_COMPOSITE_CONDITIONS[0]:
        plate = stimulus["visual_complementary_plate_png"]
        wav = None
        audio_content = "none"
    elif condition == VISUAL_ALIGNED_SILENT_CONDITION:
        plate = stimulus["visual_aligned_plate_png"]
        wav = None
        audio_content = "none"
    elif condition == "visual_silent":
        plate = stimulus["visual_plate_png"]
        wav = None
        audio_content = "none"
    else:
        raise ValueError(f"unknown trial condition: {condition}")
    record = {
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
        "mapping_class": stimulus.get("mapping_class"),
        "choice_rule": stimulus.get("choice_rule"),
    }
    if "aligned_displacement_audio_pixels" in stimulus:
        record.update({
            "aligned_displacement_audio_dx": stimulus.get(
                "aligned_displacement_audio_dx"
            ),
            "aligned_displacement_audio_dy": stimulus.get(
                "aligned_displacement_audio_dy"
            ),
            "aligned_displacement_audio_pixels": stimulus.get(
                "aligned_displacement_audio_pixels"
            ),
            "aligned_displacement_plate_pixels": stimulus.get(
                "aligned_displacement_plate_pixels"
            ),
        })
    return record


def manifest_is_complete(manifest: dict, root: Path) -> bool:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return False
    if manifest.get("render_version") != RENDER_VERSION:
        return False
    if manifest.get("audio_render_version") != AUDIO_RENDER_VERSION:
        return False
    signal_mode = manifest.get("settings", {}).get("signalMode")
    audio_expected = signal_mode in {"ir", "mixed", "mixed-aligned", "paired"}
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
    if signal_mode == "mixed-aligned":
        verification = manifest.get("combinatorial_verification")
        if not isinstance(verification, dict) or verification.get("verified") is not True:
            return False
        density_balance = verification.get("identity_density_balance")
        if (
            not isinstance(density_balance, dict)
            or density_balance.get("verified") is not True
            or density_balance.get("load_basis") != "rendered_signal_dot_count"
        ):
            return False
        verified_counts = {
            condition: count
            for condition, count in verification.get("condition_counts", {}).items()
            if count
        }
        if verified_counts != manifest.get("condition_distribution"):
            return False
        aligned_stimuli = manifest.get("stimuli", [])
        carrier_pixel_counts = {
            stimulus.get("aligned_visual_carrier_occupied_pixel_count")
            for stimulus in aligned_stimuli
        }
        carrier_histograms = {
            json.dumps(
                stimulus.get("aligned_visual_carrier_radius_histogram"),
                sort_keys=True,
            )
            for stimulus in aligned_stimuli
        }
        if (
            not aligned_stimuli
            or len(carrier_pixel_counts) != 1
            or None in carrier_pixel_counts
            or len(carrier_histograms) != 1
        ):
            return False
        expected_signal_density = summarize_visible_signal_density(
            aligned_stimuli, ALIGNED_MIXED_CONDITIONS,
        )
        if (
            manifest.get("condition_assignment", {}).get(
                "visible_signal_density_balance"
            ) != expected_signal_density
        ):
            return False
    if signal_mode == "visual-aligned":
        aligned_stimuli = manifest.get("stimuli", [])
        assignment = manifest.get("condition_assignment")
        if not aligned_stimuli or not isinstance(assignment, dict):
            return False
        expected_balance = summarize_mixed_condition_balance(
            aligned_stimuli, VISUAL_COMPOSITE_CONDITIONS,
        )
        expected_density = summarize_visible_signal_density(
            aligned_stimuli, VISUAL_COMPOSITE_CONDITIONS,
        )
        if any(
            assignment.get(key) != value
            for key, value in expected_balance.items()
        ):
            return False
        if assignment.get("visible_signal_density_balance") != expected_density:
            return False
        if assignment.get("condition_counts") != manifest.get(
            "condition_distribution"
        ):
            return False
    for stimulus in manifest.get("stimuli", []):
        if (
            signal_mode == "mixed"
            and stimulus.get("assigned_condition")
            not in {"visual_background_audio", "ir_audio"}
        ):
            return False
        if (
            signal_mode == "mixed-aligned"
            and stimulus.get("assigned_condition") not in ALIGNED_MIXED_CONDITIONS
        ):
            return False
        if (
            signal_mode == "visual-aligned"
            and stimulus.get("assigned_condition") not in VISUAL_COMPOSITE_CONDITIONS
        ):
            return False
        aligned_visual_stimulus = (
            signal_mode == "visual-aligned"
            or signal_mode == "mixed-aligned"
            and stimulus.get("assigned_condition") in ALIGNED_IDENTITY_CONDITIONS
        )
        visual_complementary_stimulus = (
            signal_mode == "visual-aligned"
            or signal_mode == "mixed-aligned"
            and stimulus.get("assigned_condition") == "visual_background_audio"
        )
        if signal_mode in {"mixed-aligned", "visual-aligned"}:
            condition = stimulus.get("assigned_condition")
            source_ids = stimulus.get("source_ids", [])
            expected_base_colours = [
                list(colour) for colour in SOURCE_COLOURS[:len(source_ids)]
            ]
            if (
                not 1 <= len(source_ids) <= len(SOURCE_COLOURS)
                or stimulus.get("aligned_visual_palette_version")
                != ALIGNED_VISUAL_PALETTE_VERSION
                or stimulus.get("visible_base_colours")
                != expected_base_colours
            ):
                return False
            identity_condition = (
                signal_mode == "mixed-aligned"
                and condition in ALIGNED_IDENTITY_CONDITIONS
            )
            if identity_condition:
                if (
                    stimulus.get("mapping_class") != "identity"
                    or stimulus.get("changed_count") != 0
                    or stimulus.get("source_ids") != stimulus.get("target_ids")
                    or stimulus.get("decoy_choice_id") is not None
                ):
                    return False
            elif signal_mode == "mixed-aligned" and (
                stimulus.get("mapping_class") != "changed"
                or not isinstance(stimulus.get("changed_count"), int)
                or stimulus.get("changed_count") < 1
                or stimulus.get("source_ids") == stimulus.get("target_ids")
            ):
                return False
            if signal_mode == "visual-aligned" and (
                stimulus.get("mapping_class") != "changed"
                or not isinstance(stimulus.get("changed_count"), int)
                or stimulus.get("changed_count") < 1
                or stimulus.get("source_ids") == stimulus.get("target_ids")
            ):
                return False
            choices = [
                tuple(choice.get("target_ids", ()))
                for choice in stimulus.get("response_choices", [])
            ]
            if len(choices) != 4 or len(set(choices)) != 4:
                return False
            if choices.count(tuple(stimulus.get("target_ids", ()))) != 1:
                return False
        for key in (
            "ir_plate_png", "visual_plate_png", "ir_input_png",
            "background_input_png",
        ):
            if not (root / stimulus[key]).is_file():
                return False
        if signal_mode in {"mixed-aligned", "visual-aligned"}:
            balanced_plate = stimulus.get("balanced_carrier_ir_plate_png")
            expected_visible_signal_dot_count = (
                stimulus.get("aligned_visual_base_dot_count")
                if signal_mode == "visual-aligned"
                else stimulus.get("balanced_visual_source_dot_count")
            )
            carrier_histogram = stimulus.get(
                "aligned_visual_carrier_radius_histogram"
            )
            carrier_a_histogram = (
                carrier_histogram.get("channel_a")
                if isinstance(carrier_histogram, dict) else None
            )
            carrier_b_histogram = (
                carrier_histogram.get("channel_b")
                if isinstance(carrier_histogram, dict) else None
            )
            if not balanced_plate or not (root / balanced_plate).is_file():
                return False
            if (
                stimulus.get("aligned_visual_carrier_version")
                != ALIGNED_VISUAL_CARRIER_VERSION
                or stimulus.get("aligned_visual_density_equivalence_version")
                != ALIGNED_VISUAL_DENSITY_EQUIVALENCE_VERSION
                or stimulus.get("aligned_visual_pair_axis")
                != ALIGNED_VISUAL_PAIR_AXIS
                or stimulus.get("aligned_visual_dot_pitch_pixels")
                != ALIGNED_VISUAL_DOT_STEP
                or stimulus.get("aligned_visual_pair_offset_pixels")
                != ALIGNED_VISUAL_PAIR_OFFSET_PIXELS
                or stimulus.get("aligned_visual_subdot_radii")
                != list(ALIGNED_VISUAL_SUBDOT_RADII)
                or not isinstance(carrier_a_histogram, dict)
                or carrier_a_histogram != carrier_b_histogram
                or carrier_a_histogram
                != ALIGNED_VISUAL_CARRIER_RADIUS_HISTOGRAM
                or sum(carrier_a_histogram.values())
                != stimulus.get("aligned_visual_carrier_dot_count")
                or stimulus.get("aligned_visual_carrier_dot_count")
                != ALIGNED_VISUAL_CARRIER_DOT_COUNT
                or stimulus.get("aligned_visual_subdot_count")
                != stimulus.get("aligned_visual_carrier_dot_count", 0) * 2
                or stimulus.get(
                    "aligned_visual_carrier_occupied_pixel_count", 0,
                ) != ALIGNED_VISUAL_CARRIER_OCCUPIED_PIXEL_COUNT
                or not stimulus.get("balanced_carrier_occupancy_sha256")
                or stimulus.get("balanced_visual_source_dot_count", 0) <= 0
                or stimulus.get("visible_signal_dot_count")
                != expected_visible_signal_dot_count
                or not isinstance(
                    stimulus.get("balanced_visual_source_radius_histogram"), dict,
                )
                or stimulus.get("balanced_visual_source_radius_area_units", 0) <= 0
                or stimulus.get("balanced_visual_source_active_pixel_count", 0) <= 0
            ):
                return False
        if visual_complementary_stimulus:
            complementary_plate = stimulus.get(
                "visual_complementary_plate_png"
            )
            complementary_source_histogram = stimulus.get(
                "visual_complementary_source_radius_histogram"
            )
            complementary_addition_histogram = stimulus.get(
                "visual_complementary_addition_radius_histogram"
            )
            if (
                not complementary_plate
                or not (root / complementary_plate).is_file()
                or stimulus.get("visual_complementary_equivalence_version")
                != "source-plus-diagnostic-equals-target-v1"
                or stimulus.get("visual_complementary_addition_colour")
                != list(ALIGNED_VISUAL_COPY_COLOUR)
                or stimulus.get("visual_complementary_carrier_occupancy_sha256")
                != stimulus.get("balanced_carrier_occupancy_sha256")
                or (
                    stimulus.get("canonical_carrier_occupancy_sha256") is not None
                    and stimulus.get(
                        "visual_complementary_carrier_occupancy_sha256"
                    ) != stimulus.get("canonical_carrier_occupancy_sha256")
                )
                or stimulus.get("visual_complementary_source_dot_count")
                != stimulus.get("balanced_visual_source_dot_count")
                or stimulus.get("visual_complementary_source_active_pixel_count")
                != stimulus.get("balanced_visual_source_active_pixel_count")
                or complementary_source_histogram
                != stimulus.get("balanced_visual_source_radius_histogram")
                or not isinstance(complementary_addition_histogram, dict)
                or sum(complementary_addition_histogram.values())
                != stimulus.get("visual_complementary_addition_dot_count")
                or stimulus.get("visual_complementary_addition_dot_count", 0) <= 0
                or stimulus.get(
                    "visual_complementary_addition_active_pixel_count", 0,
                ) <= 0
            ):
                return False
            try:
                source_mask, diagnostic_mask, target_mask = difference_mask(
                    stimulus["source_ids"], stimulus["target_ids"],
                )
            except (KeyError, TypeError, ValueError):
                return False
            if (
                stimulus.get("visual_complementary_source_mask_sha256")
                != mask_digest(source_mask)
                or stimulus.get("visual_complementary_addition_mask_sha256")
                != mask_digest(diagnostic_mask)
                or stimulus.get("visual_complementary_target_mask_sha256")
                != mask_digest(target_mask)
            ):
                return False
        if aligned_visual_stimulus:
            for key in (
                "canonical_visual_plate_png", "visual_aligned_plate_png",
                "aligned_input_png",
            ):
                if not (root / stimulus[key]).is_file():
                    return False
            if stimulus.get("aligned_displacement_audio_pixels") != (
                ALIGNED_DISPLACEMENT_AUDIO_PIXELS
            ):
                return False
            if (
                stimulus.get("alignment_equivalence_version")
                != "canonical-target-mask-v1"
                or stimulus.get("aligned_visual_base_channel_position")
                != "seeded-diagonal-a"
                or stimulus.get("aligned_visual_shifted_channel_position")
                != "seeded-diagonal-b"
                or stimulus.get("aligned_visual_base_colours")
                != expected_base_colours
                or stimulus.get("aligned_visual_copy_colour")
                != list(ALIGNED_VISUAL_COPY_COLOUR)
                or stimulus.get("canonical_visual_dot_count")
                != stimulus.get("aligned_visual_base_dot_count")
                or stimulus.get("canonical_visual_dot_count")
                != stimulus.get("visible_signal_dot_count")
                or stimulus.get("aligned_visual_base_dot_count")
                != stimulus.get("aligned_visual_shifted_dot_count")
                or stimulus.get("aligned_visual_base_radius_histogram")
                != stimulus.get("aligned_visual_shifted_radius_histogram")
                or stimulus.get("aligned_visual_base_radius_area_units")
                != stimulus.get("aligned_visual_shifted_radius_area_units")
                or stimulus.get("aligned_visual_base_active_pixel_count")
                != stimulus.get("aligned_visual_shifted_active_pixel_count")
                or stimulus.get("canonical_carrier_occupancy_sha256")
                != stimulus.get("aligned_carrier_occupancy_sha256")
                or stimulus.get("canonical_carrier_occupancy_sha256")
                != stimulus.get("balanced_carrier_occupancy_sha256")
                or stimulus.get("canonical_target_mask_sha256")
                != stimulus.get("aligned_visual_base_mask_sha256")
                or stimulus.get("aligned_target_mask_sha256")
                != stimulus.get("aligned_visual_shifted_mask_sha256")
                or stimulus.get("canonical_target_pixel_count")
                != stimulus.get("aligned_target_pixel_count")
            ):
                return False
            try:
                canonical_mask = draw_geometry_mask(stimulus["target_ids"])
                shifted_mask = translate_mask_without_clipping(
                    canonical_mask,
                    stimulus.get("aligned_displacement_audio_dx"),
                    stimulus.get("aligned_displacement_audio_dy"),
                )
            except (KeyError, TypeError, ValueError):
                return False
            if (
                stimulus.get("canonical_target_mask_sha256")
                != mask_digest(canonical_mask)
                or stimulus.get("aligned_target_mask_sha256")
                != mask_digest(shifted_mask)
                or stimulus.get("canonical_target_pixel_count")
                != sum(pixel > 0 for pixel in canonical_mask.tobytes())
                or stimulus.get("aligned_target_pixel_count")
                != sum(pixel > 0 for pixel in shifted_mask.tobytes())
            ):
                return False
            try:
                aligned_values = Image.open(
                    root / stimulus["aligned_input_png"]
                ).convert("L").tobytes()
                background_values = Image.open(
                    root / stimulus["background_input_png"]
                ).convert("L").tobytes()
                shifted_values = shifted_mask.tobytes()
                if any(
                    (aligned != background) != (mask_value > 0)
                    for aligned, background, mask_value in zip(
                        aligned_values, background_values, shifted_values,
                    )
                ):
                    return False
            except (KeyError, OSError):
                return False
        for choice in stimulus.get("response_choices", []):
            if not (root / choice["png"]).is_file():
                return False
        for key in ("ir_probe_wav", "background_wav", "aligned_target_wav"):
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
                    signal_mode == "mixed-aligned"
                    and key == "ir_probe_wav"
                    and stimulus.get("assigned_condition") == "ir_audio"
                ):
                    return False
                if (
                    signal_mode == "mixed-aligned"
                    and key == "background_wav"
                    and stimulus.get("assigned_condition") in {
                        "visual_background_audio", "visual_aligned_overlay",
                    }
                ):
                    return False
                if (
                    signal_mode == "mixed-aligned"
                    and key == "aligned_target_wav"
                    and stimulus.get("assigned_condition")
                    == "visual_aligned_ir_audio"
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
    if signal_mode in {"mixed", "mixed-aligned", "visual-aligned"}:
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
        elif condition == VISUAL_COMPOSITE_CONDITIONS[0]:
            expected_plate = stimulus.get("visual_complementary_plate_png")
            expected_wav = None
        elif condition == VISUAL_ALIGNED_SILENT_CONDITION:
            expected_plate = stimulus.get("visual_aligned_plate_png")
            expected_wav = None
        elif condition == "visual_background_audio":
            expected_plate = stimulus.get(
                "visual_complementary_plate_png",
                stimulus.get(
                    "canonical_visual_plate_png", stimulus.get("visual_plate_png"),
                ),
            )
            expected_wav = stimulus.get("background_wav")
        elif condition == "visual_aligned_overlay":
            expected_plate = stimulus.get("visual_aligned_plate_png")
            expected_wav = stimulus.get("background_wav")
        elif condition == "visual_aligned_ir_audio":
            expected_plate = stimulus.get("canonical_visual_plate_png")
            expected_wav = stimulus.get("aligned_target_wav")
        elif condition == "ir_audio":
            expected_plate = stimulus.get(
                "balanced_carrier_ir_plate_png", stimulus.get("ir_plate_png"),
            )
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
        or signal_mode in {"mixed", "mixed-aligned"}
        and assigned_condition == "ir_audio"
    )
    background_required = (
        signal_mode == "paired"
        or signal_mode == "mixed"
        and assigned_condition == "visual_background_audio"
        or signal_mode == "mixed-aligned"
        and assigned_condition in {
            "visual_background_audio", "visual_aligned_overlay",
        }
    )
    aligned_required = (
        signal_mode == "mixed-aligned"
        and assigned_condition == "visual_aligned_ir_audio"
    )
    if not probe_required and not background_required and not aligned_required:
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

    aligned_metrics = counterfactuals.get("aligned_target")
    if aligned_required:
        if not _audio_metrics_are_valid(
            aligned_metrics,
            shared_gain,
            peak_ceiling,
            retained=True,
        ):
            return False
    elif aligned_metrics is not None:
        return False

    rms_manifest = stimulus.get("wav_rms_int16")
    peak_manifest = stimulus.get("wav_peak_int16")
    if not isinstance(rms_manifest, dict) or not isinstance(peak_manifest, dict):
        return False
    retained = []
    if probe_required:
        retained.append((stimulus.get("ir_probe_wav"), probe_metrics))
    if aligned_required:
        retained.append((stimulus.get("aligned_target_wav"), aligned_metrics))
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
        choices=(
            "visual", "visual-aligned", "ir", "mixed", "mixed-aligned", "paired",
        ),
        default="mixed",
    )
    parser.add_argument("--stimuli", type=int, default=30)
    parser.add_argument(
        "--glyph-composition", choices=("automatic", "1", "2", "3"),
        default="automatic",
    )
    parser.add_argument(
        "--progression", choices=PROGRESSION_MODES, default="growing",
    )
    parser.add_argument(
        "--feedback", action=argparse.BooleanOptionalAction, default=True,
    )
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--mixed-condition-ratio", default="1:1:1:2")
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
            "mixedConditionRatio": arguments.mixed_condition_ratio,
        },
        arguments.out,
    )
    print(path)
