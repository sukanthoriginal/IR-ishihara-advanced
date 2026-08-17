#!/usr/bin/env python3
"""Generate a low-to-high colour/IR metamer curriculum.

Every stimulus has two valid perceptual readings. Its retained RGB scaffold is
a complete, familiar decoy glyph. A diagnostic feature carried by the final
probe channel transforms that decoy into a different target glyph. In the
all-visible comparator the feature is G, B, or Y; in the crossmodal condition
the exact same feature is carried only by IR audio. A probe-blind observer can
therefore make a systematic, high-confidence decoy response instead of merely
guessing among feature locations.

Shape complexity and channel count remain independently selectable. Recipes
progress from R+IR/G+IR to R+G+IR and R+G+B+IR composites.

The IR component is passed through the repository's real ``raspivoice`` binary.
A second IR file preserves its exact histogram and energy while destroying its
spatial geometry.

Usage:
    python3 generate_ishihara_stimuli.py
    python3 generate_ishihara_stimuli.py --skip-audio  # asset/UI development
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent
IMG_W, IMG_H = 178, 64
WAV_TOTAL_TIME_S = 1.05
WAV_SAMPLE_FREQ_HZ = 48000
WAV_CHANNELS = 2
WAV_BYTES_PER_SAMPLE = 2
WAV_HEADER_BYTES = 44
WAV_SAMPLE_COUNT = round(WAV_TOTAL_TIME_S * WAV_SAMPLE_FREQ_HZ)
WAV_SAMPLES_PER_COLUMN = WAV_SAMPLE_COUNT // IMG_W
EXPECTED_WAV_BYTES = (
    WAV_HEADER_BYTES
    + WAV_SAMPLE_COUNT * WAV_CHANNELS * WAV_BYTES_PER_SAMPLE
)
RASPIVOICE_MAX_WAIT_S = 6.0
RASPIVOICE_POLL_INTERVAL_S = 0.02
SHIM_DIR = ROOT / ".aplay_shim"
DEFAULT_RASPIVOICE_BIN = Path(
    os.environ.get(
        "RASPIVOICE_BIN",
        ROOT.parent / "IR-vOICe" / "raspivoice" / "Release" / "raspivoice",
    )
)


PLATE_SCALE = 4
PLATE_W = IMG_W * PLATE_SCALE
PLATE_H = IMG_H * PLATE_SCALE
DOT_STEP = 16
PROBE_DOT_RADIUS = 7

METAMER_FAMILIES = {
    1: {
        "family_id": "factorial-pairs",
        "label": "Two-bit factorial pairs",
        "description": "Base, left-only, right-only, and both-edited glyph pairs",
        "transformations": (
            "fp-to-ep", "ct-to-ci", "co-to-gq", "vt-to-yt",
        ),
    },
    2: {
        "family_id": "completion-forks",
        "label": "Alternative completion forks",
        "description": "The same scaffold supports several coherent probe completions",
        "transformations": (
            "f-to-e-fork", "f-to-p-fork", "f-to-r-fork", "c-to-g-fork",
        ),
    },
    3: {
        "family_id": "multistroke-factorials",
        "label": "Multi-stroke factorials",
        "description": "Two independent complex edits create four matched pair readings",
        "transformations": (
            "pl-to-bl", "three-l-to-three-e",
            "p3-to-b8", "l3-to-e3",
        ),
    },
    4: {
        "family_id": "ternary-chimeras",
        "label": "Three-way chimeric branches",
        "description": "The probe changes one of three positions; all completions are plausible",
        "transformations": (
            "fpt-to-frt", "cot-to-cqt", "vct-to-vci", "plc-to-blc",
        ),
    },
}

COMPLEXITY_TIERS = {
    level: {
        "family_id": family["family_id"],
        "label": family["label"],
        "description": family["description"],
        "glyphs": family["transformations"],
    }
    for level, family in METAMER_FAMILIES.items()
}

CHANNEL_RECIPES = {
    "r-ir": {
        "label": "R + IR",
        "visible_channels": ("R", "G"),
        "crossmodal_channels": ("R", "IR"),
    },
    "g-ir": {
        "label": "G + IR",
        "visible_channels": ("G", "B"),
        "crossmodal_channels": ("G", "IR"),
    },
    "rg-ir": {
        "label": "R + G + IR",
        "visible_channels": ("R", "G", "B"),
        "crossmodal_channels": ("R", "G", "IR"),
    },
    "rgb-ir": {
        "label": "R + G + B + IR",
        "visible_channels": ("R", "G", "B", "Y"),
        "crossmodal_channels": ("R", "G", "B", "IR"),
    },
}

CURRICULUM_RECIPE_BY_COMPLEXITY = {
    1: "r-ir",
    2: "g-ir",
    3: "rg-ir",
    4: "rgb-ir",
}

ALL_GLYPHS = tuple(
    glyph
    for tier in COMPLEXITY_TIERS.values()
    for glyph in tier["glyphs"]
)

SYMBOL_SEGMENTS = {
    "F": ("top", "middle", "left-upper", "left-lower"),
    "E": ("top", "middle", "bottom", "left-upper", "left-lower"),
    "P": ("top", "middle", "left-upper", "left-lower", "right-upper"),
    "R": (
        "top", "middle", "left-upper", "left-lower", "right-upper",
        "right-leg",
    ),
    "B": (
        "top", "middle", "bottom", "left-upper", "left-lower",
        "right-upper", "right-lower",
    ),
    "C": ("top", "bottom", "left-upper", "left-lower"),
    "G": (
        "top", "bottom", "left-upper", "left-lower", "half-middle",
        "right-lower",
    ),
    "O": (
        "top", "bottom", "left-upper", "left-lower", "right-upper",
        "right-lower",
    ),
    "Q": (
        "top", "bottom", "left-upper", "left-lower", "right-upper",
        "right-lower", "tail",
    ),
    "U": ("bottom", "left-upper", "left-lower", "right-upper", "right-lower"),
    "T": ("top", "center"),
    "I": ("top", "center", "bottom"),
    "L": ("left-upper", "left-lower", "bottom"),
    "3": ("top", "middle", "bottom", "right-upper", "right-lower"),
    "8": (
        "top", "middle", "bottom", "left-upper", "left-lower",
        "right-upper", "right-lower",
    ),
    "6": (
        "top", "middle", "bottom", "left-upper", "left-lower",
        "right-lower",
    ),
    "9": (
        "top", "middle", "bottom", "left-upper", "right-upper",
        "right-lower",
    ),
    "V": ("vee-left", "vee-right"),
    "Y": ("vee-left", "vee-right", "stem"),
    "CARET": ("caret-left", "caret-right"),
    "A": ("caret-left", "caret-right", "crossbar"),
}

TRANSFORMATIONS = {
    # Level 1: the four responses are a complete 2x2 crossing, while the
    # correct state varies across left-only, right-only, and both-edited.
    "fp-to-ep": {
        "base": ("F", "P"), "target": ("E", "P"),
        "choices": ("pair-ep", "pair-fp", "pair-fr", "pair-er"),
        "choice_structure": "factorial-2x2", "probe_state": "left-only",
    },
    "ct-to-ci": {
        "base": ("C", "T"), "target": ("C", "I"),
        "choices": ("pair-ci", "pair-ct", "pair-gt", "pair-gi"),
        "choice_structure": "factorial-2x2", "probe_state": "right-only",
    },
    "co-to-gq": {
        "base": ("C", "O"), "target": ("G", "Q"),
        "choices": ("pair-gq", "pair-co", "pair-go", "pair-cq"),
        "choice_structure": "factorial-2x2", "probe_state": "both",
    },
    "vt-to-yt": {
        "base": ("V", "T"), "target": ("Y", "T"),
        "choices": ("pair-yt", "pair-vt", "pair-vi", "pair-yi"),
        "choice_structure": "factorial-2x2", "probe_state": "left-only",
    },

    # Level 2: a matched base has several legitimate continuations. The three
    # F trials are deliberately identical until probe geometry resolves E/P/R.
    "f-to-e-fork": {
        "base": ("F",), "target": ("E",),
        "choices": ("glyph-e", "glyph-f", "glyph-p", "glyph-r"),
        "choice_structure": "completion-fork", "probe_state": "bottom-bar",
    },
    "f-to-p-fork": {
        "base": ("F",), "target": ("P",),
        "choices": ("glyph-p", "glyph-f", "glyph-e", "glyph-r"),
        "choice_structure": "completion-fork", "probe_state": "upper-bowl",
    },
    "f-to-r-fork": {
        "base": ("F",), "target": ("R",),
        "choices": ("glyph-r", "glyph-f", "glyph-e", "glyph-p"),
        "choice_structure": "completion-fork", "probe_state": "bowl-and-leg",
    },
    "c-to-g-fork": {
        "base": ("C",), "target": ("G",),
        "choices": ("glyph-g", "glyph-c", "glyph-o", "glyph-q"),
        "choice_structure": "completion-fork", "probe_state": "inner-hook",
    },

    # Level 3: the same factorial logic now uses coordinated, multi-stroke
    # changes. Partial interpretations remain explicit response options.
    "pl-to-bl": {
        "base": ("P", "L"), "target": ("B", "L"),
        "choices": ("pair-bl", "pair-pl", "pair-pe", "pair-be"),
        "choice_structure": "multistroke-factorial", "probe_state": "left-only",
    },
    "three-l-to-three-e": {
        "base": ("3", "L"), "target": ("3", "E"),
        "choices": ("pair-3e", "pair-3l", "pair-8l", "pair-8e"),
        "choice_structure": "multistroke-factorial", "probe_state": "right-only",
    },
    "p3-to-b8": {
        "base": ("P", "3"), "target": ("B", "8"),
        "choices": ("pair-b8", "pair-p3", "pair-b3", "pair-p8"),
        "choice_structure": "multistroke-factorial", "probe_state": "both",
    },
    "l3-to-e3": {
        "base": ("L", "3"), "target": ("E", "3"),
        "choices": ("pair-e3", "pair-l3", "pair-l8", "pair-e8"),
        "choice_structure": "multistroke-factorial", "probe_state": "left-only",
    },

    # Level 4: three positions each have a valid completion, but exactly one is
    # selected. The three non-decoy choices are equally "more complete".
    "fpt-to-frt": {
        "base": ("F", "P", "T"), "target": ("F", "R", "T"),
        "choices": ("triple-frt", "triple-fpt", "triple-ept", "triple-fpi"),
        "choice_structure": "one-of-three-position", "probe_state": "middle",
    },
    "cot-to-cqt": {
        "base": ("C", "O", "T"), "target": ("C", "Q", "T"),
        "choices": ("triple-cqt", "triple-cot", "triple-got", "triple-coi"),
        "choice_structure": "one-of-three-position", "probe_state": "middle",
    },
    "vct-to-vci": {
        "base": ("V", "C", "T"), "target": ("V", "C", "I"),
        "choices": ("triple-vci", "triple-vct", "triple-yct", "triple-vgt"),
        "choice_structure": "one-of-three-position", "probe_state": "right",
    },
    "plc-to-blc": {
        "base": ("P", "L", "CARET"), "target": ("B", "L", "CARET"),
        "choices": (
            "triple-b-l-caret", "triple-p-l-caret",
            "triple-p-e-caret", "triple-p-l-a",
        ),
        "choice_structure": "one-of-three-position", "probe_state": "left",
    },
}

CHOICE_SYMBOLS = {
    **{f"glyph-{symbol.lower()}": (symbol,) for symbol in SYMBOL_SEGMENTS},
    "pair-er": ("E", "R"), "pair-ep": ("E", "P"),
    "pair-fr": ("F", "R"), "pair-fp": ("F", "P"),
    "pair-gi": ("G", "I"), "pair-gt": ("G", "T"),
    "pair-ci": ("C", "I"), "pair-ct": ("C", "T"),
    "pair-gq": ("G", "Q"), "pair-go": ("G", "O"),
    "pair-cq": ("C", "Q"), "pair-co": ("C", "O"),
    "pair-yi": ("Y", "I"), "pair-yt": ("Y", "T"),
    "pair-vi": ("V", "I"), "pair-vt": ("V", "T"),
    "pair-pl": ("P", "L"), "pair-bl": ("B", "L"),
    "pair-pe": ("P", "E"), "pair-be": ("B", "E"),
    "pair-3-caret": ("3", "CARET"), "pair-3-a": ("3", "A"),
    "pair-8-caret": ("8", "CARET"), "pair-8-a": ("8", "A"),
    "pair-p3": ("P", "3"), "pair-b3": ("B", "3"),
    "pair-p8": ("P", "8"), "pair-b8": ("B", "8"),
    "pair-3l": ("3", "L"), "pair-3e": ("3", "E"),
    "pair-8l": ("8", "L"), "pair-8e": ("8", "E"),
    "pair-l3": ("L", "3"), "pair-e3": ("E", "3"),
    "pair-l8": ("L", "8"), "pair-e8": ("E", "8"),
    "pair-l-caret": ("L", "CARET"), "pair-e-caret": ("E", "CARET"),
    "pair-l-a": ("L", "A"), "pair-e-a": ("E", "A"),
    "triple-fpt": ("F", "P", "T"), "triple-ept": ("E", "P", "T"),
    "triple-frt": ("F", "R", "T"), "triple-fpi": ("F", "P", "I"),
    "triple-cot": ("C", "O", "T"), "triple-got": ("G", "O", "T"),
    "triple-cqt": ("C", "Q", "T"), "triple-coi": ("C", "O", "I"),
    "triple-vct": ("V", "C", "T"), "triple-yct": ("Y", "C", "T"),
    "triple-vgt": ("V", "G", "T"), "triple-vci": ("V", "C", "I"),
    "triple-p-l-caret": ("P", "L", "CARET"),
    "triple-b-l-caret": ("B", "L", "CARET"),
    "triple-p-e-caret": ("P", "E", "CARET"),
    "triple-p-l-a": ("P", "L", "A"),
}

CHOICE_LABELS = {
    choice_id: "".join("∧" if symbol == "CARET" else symbol for symbol in symbols)
    for choice_id, symbols in CHOICE_SYMBOLS.items()
}

for transformation_id, transformation in TRANSFORMATIONS.items():
    transformation["target_choice"] = transformation["choices"][0]
    transformation["decoy_choice"] = transformation["choices"][1]

ALL_RESPONSE_CHOICES = tuple(sorted(CHOICE_SYMBOLS))


def ensure_aplay_shim():
    """Prevent raspivoice from repeatedly invoking unavailable macOS aplay."""
    SHIM_DIR.mkdir(exist_ok=True)
    shim = SHIM_DIR / "aplay"
    if not shim.exists():
        shim.write_text("#!/bin/sh\nexit 0\n")
        shim.chmod(0o755)


def run_raspivoice(png_path: Path, wav_path: Path, raspivoice_bin: Path, retries=3):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            _run_raspivoice_once(png_path, wav_path, raspivoice_bin)
            return
        except RuntimeError as error:
            last_error = error
            print(f"  retry {attempt}/{retries} after: {error}")
    raise last_error


def _run_raspivoice_once(png_path: Path, wav_path: Path, raspivoice_bin: Path):
    wav_path.unlink(missing_ok=True)
    env = os.environ.copy()
    env["PATH"] = f"{SHIM_DIR}:{env.get('PATH', '')}"
    process = subprocess.Popen(
        [
            str(raspivoice_bin),
            "-s0",
            "-i", str(png_path),
            "-o", str(wav_path),
            "-r", str(IMG_H),
            "-c", str(IMG_W),
            "-t", str(WAV_TOTAL_TIME_S),
            "-Z", str(WAV_SAMPLE_FREQ_HZ),
            "--no_record",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    deadline = time.monotonic() + RASPIVOICE_MAX_WAIT_S
    while time.monotonic() < deadline:
        if wav_path.exists() and wav_path.stat().st_size == EXPECTED_WAV_BYTES:
            break
        time.sleep(RASPIVOICE_POLL_INTERVAL_S)

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

    actual = wav_path.stat().st_size if wav_path.exists() else None
    if actual != EXPECTED_WAV_BYTES:
        raise RuntimeError(
            f"raspivoice wrote a bad WAV for {png_path} "
            f"(expected {EXPECTED_WAV_BYTES} bytes, got {actual or 'missing'})"
        )


def _normal_point(x: float, y: float) -> tuple[int, int]:
    """Map normalized glyph coordinates onto the 178x64 soundscape frame."""
    return round(x * IMG_W), round(y * IMG_H)




def _symbol_boxes(symbol_count: int):
    if symbol_count == 1:
        return ((0.35, 0.08, 0.65, 0.92),)
    if symbol_count == 2:
        return (
            (0.25, 0.08, 0.47, 0.92),
            (0.53, 0.08, 0.75, 0.92),
        )
    if symbol_count == 3:
        return (
            (0.17, 0.08, 0.35, 0.92),
            (0.41, 0.08, 0.59, 0.92),
            (0.65, 0.08, 0.83, 0.92),
        )
    raise ValueError(f"unsupported symbol count: {symbol_count}")


def _draw_symbol(draw: ImageDraw.ImageDraw, symbol: str, box, width=6):
    x0, y0, x1, y1 = box

    def point(x, y):
        return _normal_point(x0 + x * (x1 - x0), y0 + y * (y1 - y0))

    segments = {
        "top": (point(0.12, 0.08), point(0.88, 0.08)),
        "middle": (point(0.12, 0.50), point(0.88, 0.50)),
        "half-middle": (point(0.50, 0.50), point(0.88, 0.50)),
        "bottom": (point(0.12, 0.92), point(0.88, 0.92)),
        "left-upper": (point(0.12, 0.08), point(0.12, 0.50)),
        "left-lower": (point(0.12, 0.50), point(0.12, 0.92)),
        "right-upper": (point(0.88, 0.08), point(0.88, 0.50)),
        "right-lower": (point(0.88, 0.50), point(0.88, 0.92)),
        "center": (point(0.50, 0.08), point(0.50, 0.92)),
        "right-leg": (point(0.50, 0.50), point(0.90, 0.92)),
        "tail": (point(0.58, 0.67), point(0.96, 1.00)),
        "vee-left": (point(0.10, 0.08), point(0.50, 0.52)),
        "vee-right": (point(0.90, 0.08), point(0.50, 0.52)),
        "stem": (point(0.50, 0.52), point(0.50, 0.96)),
        "caret-left": (point(0.10, 0.92), point(0.50, 0.08)),
        "caret-right": (point(0.90, 0.92), point(0.50, 0.08)),
        "crossbar": (point(0.29, 0.58), point(0.71, 0.58)),
    }
    for segment in SYMBOL_SEGMENTS[symbol]:
        draw.line(segments[segment], fill=255, width=width, joint="curve")


def _make_symbols_mask(symbols: tuple[str, ...]) -> Image.Image:
    mask = Image.new("L", (IMG_W, IMG_H), 0)
    draw = ImageDraw.Draw(mask)
    for symbol, box in zip(symbols, _symbol_boxes(len(symbols))):
        _draw_symbol(draw, symbol, box)
    return mask


def make_variant_masks(variant_id: str) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Return decoy scaffold, probe edit, and transformed target masks."""
    transformation = TRANSFORMATIONS.get(variant_id)
    if transformation is None:
        raise ValueError(f"unknown metamer transformation: {variant_id}")
    scaffold = _make_symbols_mask(transformation["base"])
    intended_full = _make_symbols_mask(transformation["target"])
    if ImageChops.subtract(scaffold, intended_full).getbbox() is not None:
        raise RuntimeError(
            f"base is not a strict subset of target for {variant_id}"
        )
    diagnostic = ImageChops.subtract(intended_full, scaffold)
    full = ImageChops.lighter(scaffold, diagnostic)
    return scaffold, diagnostic, full


def make_glyph_mask(choice_id: str) -> Image.Image:
    """Return a monochrome response-choice mask."""
    try:
        return _make_symbols_mask(CHOICE_SYMBOLS[choice_id])
    except KeyError as error:
        raise ValueError(f"unknown response choice: {choice_id}") from error


def make_dot_layout(rng: np.random.Generator) -> list[tuple[int, int, int]]:
    dots = []
    for base_y in range(DOT_STEP // 2, PLATE_H, DOT_STEP):
        for base_x in range(DOT_STEP // 2, PLATE_W, DOT_STEP):
            x = int(np.clip(base_x + rng.integers(-5, 6), 4, PLATE_W - 5))
            y = int(np.clip(base_y + rng.integers(-5, 6), 4, PLATE_H - 5))
            radius = int(rng.integers(5, 10))
            dots.append((x, y, radius))
    rng.shuffle(dots)
    return dots


def _vary_colour(base: tuple[int, int, int], rng: np.random.Generator) -> tuple[int, int, int]:
    delta = int(rng.integers(-14, 15))
    return tuple(int(np.clip(channel + delta, 0, 255)) for channel in base)


def _split_target_components(
    dots, target_flags, component_count: int, rng: np.random.Generator,
):
    """Balance N components while distributing each local group across channels."""
    components = [[False] * len(dots) for _ in range(component_count)]
    remaining = {int(index) for index in np.flatnonzero(target_flags)}

    while len(remaining) >= component_count:
        candidates = sorted(remaining)
        anchor = candidates[int(rng.integers(0, len(candidates)))]
        x, y, _ = dots[anchor]
        neighbours = sorted(
            (index for index in candidates if index != anchor),
            key=lambda index: (
                (dots[index][0] - x) ** 2 + (dots[index][1] - y) ** 2,
                index,
            ),
        )[:component_count - 1]
        local_group = [anchor, *neighbours]
        for dot_index, component_index in zip(
            local_group, rng.permutation(component_count),
        ):
            components[int(component_index)][dot_index] = True
            remaining.remove(dot_index)

    if remaining:
        counts = [sum(component) for component in components]
        available_components = sorted(
            range(component_count), key=lambda index: (counts[index], rng.random()),
        )
        for dot_index, component_index in zip(sorted(remaining), available_components):
            components[component_index][dot_index] = True

    return components


def _family_variants_for(variant_id: str) -> tuple[str, ...]:
    for tier in COMPLEXITY_TIERS.values():
        if variant_id in tier["glyphs"]:
            return tier["glyphs"]
    raise ValueError(f"variant is not assigned to a family: {variant_id}")


def _spread_subset_flags(dots, flags, target_count: int) -> list[bool]:
    """Select an evenly spread deterministic subset of flagged dot centres."""
    candidates = sorted(
        np.flatnonzero(flags), key=lambda index: (dots[index][0], dots[index][1]),
    )
    if target_count >= len(candidates):
        return [bool(flag) for flag in flags]
    if target_count == 1:
        selected = {candidates[len(candidates) // 2]}
    else:
        selected = {
            candidates[round(i * (len(candidates) - 1) / (target_count - 1))]
            for i in range(target_count)
        }
    if len(selected) != target_count:
        raise RuntimeError("diagnostic subset selection produced duplicate indices")
    return [index in selected for index in range(len(dots))]


def draw_trial_assets(glyph_id: str, recipe: dict, rng: np.random.Generator):
    # Isolate random streams so variable scaffold geometry cannot shift later
    # background, colour-jitter, or IR-energy draws. Calls that begin from the
    # same seed therefore share nuisance texture while differing only in the
    # intended glyph geometry.
    stream_seeds = rng.integers(0, np.iinfo(np.int64).max, size=5)
    layout_rng = np.random.default_rng(stream_seeds[0])
    component_rng = np.random.default_rng(stream_seeds[1])
    appearance_rng = np.random.default_rng(stream_seeds[2])
    ir_rng = np.random.default_rng(stream_seeds[3])
    scramble_rng = np.random.default_rng(stream_seeds[4])
    scaffold_low, _, _ = make_variant_masks(glyph_id)
    scaffold_high = scaffold_low.resize(
        (PLATE_W, PLATE_H), Image.Resampling.NEAREST,
    )
    dots = make_dot_layout(layout_rng)
    scaffold_flags = [
        scaffold_high.getpixel((x, y)) > 0 for x, y, _ in dots
    ]
    family_diagnostic_flags = []
    for sibling_id in _family_variants_for(glyph_id):
        _, sibling_diagnostic, _ = make_variant_masks(sibling_id)
        sibling_high = sibling_diagnostic.resize(
            (PLATE_W, PLATE_H), Image.Resampling.NEAREST,
        )
        family_diagnostic_flags.append([
            sibling_high.getpixel((x, y)) > 0 for x, y, _ in dots
        ])
    target_probe_count = min(sum(flags) for flags in family_diagnostic_flags)
    current_variant_index = _family_variants_for(glyph_id).index(glyph_id)
    diagnostic_flags = _spread_subset_flags(
        dots, family_diagnostic_flags[current_variant_index], target_probe_count,
    )

    # Every possible probe location uses a fixed dot radius, even when neutral.
    # Consequently transformation identity cannot be decoded from probe-dot
    # size even though each retained scaffold depicts a different decoy glyph.
    possible_probe = [
        any(flags[index] for flags in family_diagnostic_flags)
        for index in range(len(dots))
    ]
    dots = [
        (x, y, PROBE_DOT_RADIUS if possible_probe[index] else radius)
        for index, (x, y, radius) in enumerate(dots)
    ]

    if sum(scaffold_flags) < 18:
        raise RuntimeError(f"variant {glyph_id} produced too few scaffold dots")
    if sum(diagnostic_flags) < 3:
        raise RuntimeError(f"variant {glyph_id} produced too few diagnostic dots")
    if any(a and b for a, b in zip(scaffold_flags, diagnostic_flags)):
        raise RuntimeError(f"variant {glyph_id} has overlapping scaffold/probe dots")

    component_count = len(recipe["crossmodal_channels"])
    # The final component is always the probe: a visible comparator colour in
    # one condition and IR audio in the paired condition. All earlier channels
    # jointly carry the invariant scaffold.
    scaffold_components = _split_target_components(
        dots, scaffold_flags, component_count - 1, component_rng,
    )
    component_flags = [*scaffold_components, diagnostic_flags]
    ir_component_index = recipe["crossmodal_channels"].index("IR")
    if ir_component_index != component_count - 1:
        raise RuntimeError("IR must be the final diagnostic component")

    visual_composite = Image.new("RGB", (PLATE_W, PLATE_H), (48, 48, 48))
    visible_components = Image.new("RGB", (PLATE_W, PLATE_H), (48, 48, 48))
    neutral_plate = Image.new("RGB", (PLATE_W, PLATE_H), (48, 48, 48))
    visual_draw = ImageDraw.Draw(visual_composite)
    crossmodal_draw = ImageDraw.Draw(visible_components)
    neutral_draw = ImageDraw.Draw(neutral_plate)

    # Approximate digital luminance matches. Formal collection still requires
    # participant/display-specific equiluminance calibration.
    channel_colours = {
        "R": (181, 72, 75),
        "G": (65, 110, 70),
        "B": (66, 95, 169),
        "Y": (130, 87, 35),
    }
    neutral_greys = ((92, 92, 92), (98, 98, 98), (104, 104, 104), (86, 86, 86))

    # Precompute every dot's neutral and channel colours regardless of mask
    # membership. A shared seed prevents transformation identity from being
    # correlated with background texture or colour jitter.
    neutral_colours = [
        _vary_colour(
            neutral_greys[int(appearance_rng.integers(0, len(neutral_greys)))],
            appearance_rng,
        )
        for _ in dots
    ]
    dot_channel_colours = {
        channel: [_vary_colour(base, appearance_rng) for _ in dots]
        for channel, base in channel_colours.items()
    }

    for dot_index, (x, y, radius) in enumerate(dots):
        box = (x - radius, y - radius, x + radius, y + radius)
        neutral = neutral_colours[dot_index]
        neutral_draw.ellipse(box, fill=neutral)
        component_index = next(
            (
                index
                for index, flags in enumerate(component_flags)
                if flags[dot_index]
            ),
            None,
        )
        if component_index is None:
            visual_draw.ellipse(box, fill=neutral)
            crossmodal_draw.ellipse(box, fill=neutral)
            continue

        visible_channel = recipe["visible_channels"][component_index]
        visible_colour = dot_channel_colours[visible_channel][dot_index]
        visual_draw.ellipse(box, fill=visible_colour)
        crossmodal_channel = recipe["crossmodal_channels"][component_index]
        if crossmodal_channel == "IR":
            crossmodal_colour = neutral
        elif crossmodal_channel == visible_channel:
            # Keep retained RGB components pixel-identical across the paired
            # visual and crossmodal plates. The IR substitution must be the
            # only stimulus change in that comparison.
            crossmodal_colour = visible_colour
        else:
            crossmodal_colour = dot_channel_colours[crossmodal_channel][dot_index]
        crossmodal_draw.ellipse(box, fill=crossmodal_colour)

    aligned_ir, background_ir = _draw_ir_map(
        dots, component_flags[ir_component_index], ir_rng,
    )
    scrambled_ir = _scramble_ir_map(aligned_ir, scramble_rng)

    return (
        visual_composite,
        visible_components,
        neutral_plate,
        aligned_ir,
        scrambled_ir,
        component_flags,
        background_ir,
    )


def _draw_ir_map(
    dots, bright_flags, rng: np.random.Generator,
) -> tuple[Image.Image, Image.Image]:
    # Build one nonzero sensor-noise carrier, then add the bright probe to a
    # copy. Non-IR trials can therefore play the exact carrier without the
    # diagnostic geometry instead of replacing the auditory stream with
    # silence.
    geometry = Image.new("L", (PLATE_W, PLATE_H), 0)
    draw = ImageDraw.Draw(geometry)
    for (x, y, radius), is_bright in zip(dots, bright_flags):
        if is_bright:
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius), fill=255,
            )
    score = np.asarray(
        geometry.resize((IMG_W, IMG_H), Image.Resampling.LANCZOS),
        dtype=np.float64,
    ).reshape(-1)
    pixel_count = score.size
    bright_pixel_count = min(sum(bright_flags) * 8, pixel_count)
    background_multiset = np.clip(
        rng.normal(18, 2.5, pixel_count), 0, 255,
    ).astype(np.uint8)
    bright_values = rng.integers(
        232, 256, size=bright_pixel_count, dtype=np.uint8,
    )
    # Same tie noise for all family variants because they share the seed.
    order = np.argsort(score + rng.random(pixel_count) * 1e-6)
    # Assign the same random dark-intensity multiset in geometry-rank order.
    # The pixels replaced by the bright probe consequently hold the same dark
    # multiset in every family alternative, preserving exact histograms while
    # keeping each trial's background identical outside its own probe.
    background_values = np.empty(pixel_count, dtype=np.uint8)
    background_values[order] = background_multiset
    aligned_values = background_values.copy()
    aligned_values[order[-bright_pixel_count:]] = bright_values
    aligned = Image.fromarray(aligned_values.reshape((IMG_H, IMG_W)), mode="L")
    background = Image.fromarray(
        background_values.reshape((IMG_H, IMG_W)), mode="L",
    )
    return aligned, background


def _scramble_ir_map(aligned: Image.Image, rng: np.random.Generator) -> Image.Image:
    """Destroy global geometry while preserving the exact intensity histogram.

    Independent row and column permutations keep every pixel value from the
    aligned stimulus (and therefore total IR energy) but break the glyph's
    figure-ground topology.
    """
    values = np.asarray(aligned)
    row_order = rng.permutation(values.shape[0])
    col_order = rng.permutation(values.shape[1])
    return Image.fromarray(values[row_order][:, col_order], mode="L")


def _read_wav_samples(path: Path) -> tuple[wave._wave_params, np.ndarray]:
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        if params.sampwidth != WAV_BYTES_PER_SAMPLE:
            raise RuntimeError(f"unexpected WAV sample width in {path}")
        samples = np.frombuffer(
            source.readframes(source.getnframes()), dtype="<i2",
        ).astype(np.float64)
    return params, samples


def _write_wav_samples(path: Path, params, samples: np.ndarray):
    encoded = np.rint(samples).clip(-32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as destination:
        destination.setparams(params)
        destination.writeframes(encoded.tobytes())


def normalize_family_wav_rms(stimuli, out_dir: Path):
    """Remove whole-file loudness as a response cue within each choice family."""
    for wav_field in ("ir_wav", "ir_scrambled_wav", "ir_background_wav"):
        groups = {}
        for stimulus in stimuli:
            key = (
                stimulus["split"], stimulus["complexity_level"],
                stimulus["channel_recipe_id"], stimulus["seed"],
            )
            groups.setdefault(key, []).append(stimulus)

        rms_field = f"{wav_field}_rms_int16"
        for group in groups.values():
            loaded = []
            for stimulus in group:
                path = out_dir / stimulus[wav_field]
                params, samples = _read_wav_samples(path)
                rms = float(np.sqrt(np.mean(samples * samples)))
                if rms <= 0:
                    raise RuntimeError(f"cannot normalize silent WAV: {path}")
                loaded.append((stimulus, path, params, samples, rms))
            target_rms = min(item[4] for item in loaded)
            for stimulus, path, params, samples, rms in loaded:
                _write_wav_samples(path, params, samples * (target_rms / rms))
                _, normalized = _read_wav_samples(path)
                stimulus[rms_field] = round(
                    float(np.sqrt(np.mean(normalized * normalized))), 6,
                )


def generate(args) -> Path:
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "glyphs").mkdir(exist_ok=True)

    if not args.skip_audio:
        ensure_aplay_shim()
        raspivoice_bin = args.raspivoice_bin
        if not raspivoice_bin.exists():
            raise SystemExit(
                f"raspivoice binary not found at {raspivoice_bin}. "
                "Pass --raspivoice-bin, set RASPIVOICE_BIN, build raspivoice "
                "in this repository, or use --skip-audio."
            )
        if not os.access(raspivoice_bin, os.X_OK):
            raise SystemExit(f"raspivoice binary is not executable: {raspivoice_bin}")

    for choice_id in ALL_RESPONSE_CHOICES:
        make_glyph_mask(choice_id).save(out_dir / "glyphs" / f"{choice_id}.png")

    stimuli = []
    trial_number = 0
    layout_number = 0
    for split in ("train", "test"):
        for complexity_level, tier in COMPLEXITY_TIERS.items():
            for variant in range(args.variants_per_glyph):
                # All four transformations in a level reuse this seed. Probe
                # dot count, IR histogram, and audio energy can consequently be
                # matched even though each scaffold depicts a different decoy.
                seed = args.seed + layout_number * 1009
                for glyph_id in tier["glyphs"]:
                    for recipe_id, recipe in CHANNEL_RECIPES.items():
                        transformation = TRANSFORMATIONS[glyph_id]
                        rng = np.random.default_rng(seed)
                        stem = (
                            f"{split}_l{complexity_level}_{glyph_id}_"
                            f"{recipe_id}_{variant:02d}"
                        )
                        trial_dir = out_dir / stem
                        trial_dir.mkdir(exist_ok=True)

                        (
                            visual_composite,
                            visible_components,
                            neutral_plate,
                            aligned_ir,
                            scrambled_ir,
                            component_flags,
                            background_ir,
                        ) = draw_trial_assets(glyph_id, recipe, rng)
                        visual_composite_path = trial_dir / "visual_composite.png"
                        visible_components_path = trial_dir / "visible_components.png"
                        neutral_plate_path = trial_dir / "neutral_plate.png"
                        aligned_path = trial_dir / "ir_input.png"
                        scrambled_path = trial_dir / "ir_scrambled_input.png"
                        background_path = trial_dir / "ir_background_input.png"
                        visual_composite.save(visual_composite_path)
                        visible_components.save(visible_components_path)
                        neutral_plate.save(neutral_plate_path)
                        aligned_ir.save(aligned_path)
                        scrambled_ir.save(scrambled_path)
                        background_ir.save(background_path)

                        aligned_wav = trial_dir / "ir.wav"
                        scrambled_wav = trial_dir / "ir_scrambled.wav"
                        background_wav = trial_dir / "ir_background.wav"
                        if not args.skip_audio:
                            print(f"[{trial_number + 1}] {stem}: generating aligned audio")
                            run_raspivoice(aligned_path, aligned_wav, raspivoice_bin)
                            print(f"[{trial_number + 1}] {stem}: generating scrambled control")
                            run_raspivoice(scrambled_path, scrambled_wav, raspivoice_bin)
                            print(f"[{trial_number + 1}] {stem}: generating background carrier")
                            run_raspivoice(background_path, background_wav, raspivoice_bin)

                        stimuli.append({
                            "stimulus_id": stem,
                            "split": split,
                            "glyph_id": glyph_id,
                            "variant_id": glyph_id,
                            "variant_label": (
                                f"{CHOICE_LABELS[transformation['target_choice']]} "
                                f"from {CHOICE_LABELS[transformation['decoy_choice']]}"
                            ),
                            "transformation_id": glyph_id,
                            "target_choice_id": transformation["target_choice"],
                            "target_label": CHOICE_LABELS[
                                transformation["target_choice"]
                            ],
                            "decoy_choice_id": transformation["decoy_choice"],
                            "decoy_label": CHOICE_LABELS[
                                transformation["decoy_choice"]
                            ],
                            "response_choices": list(transformation["choices"]),
                            "choice_structure": transformation["choice_structure"],
                            "probe_state": transformation["probe_state"],
                            "family_id": tier["family_id"],
                            "seed": seed,
                            "complexity_level": complexity_level,
                            "complexity_label": tier["label"],
                            "channel_recipe_id": recipe_id,
                            "channel_recipe_label": recipe["label"],
                            "visible_channels": list(recipe["visible_channels"]),
                            "crossmodal_channels": list(recipe["crossmodal_channels"]),
                            "scaffold_channels": list(
                                recipe["crossmodal_channels"][:-1]
                            ),
                            "visible_probe_channel": recipe["visible_channels"][-1],
                            "crossmodal_probe_channel": "IR",
                            "component_count": len(component_flags),
                            "component_dot_counts": [sum(flags) for flags in component_flags],
                            "scaffold_dot_count": sum(
                                sum(flags) for flags in component_flags[:-1]
                            ),
                            "diagnostic_dot_count": sum(component_flags[-1]),
                            "visual_composite_png": str(
                                visual_composite_path.relative_to(out_dir)
                            ),
                            "visible_components_png": str(
                                visible_components_path.relative_to(out_dir)
                            ),
                            "neutral_plate_png": str(neutral_plate_path.relative_to(out_dir)),
                            "ir_input_png": str(aligned_path.relative_to(out_dir)),
                            "ir_scrambled_input_png": str(
                                scrambled_path.relative_to(out_dir)
                            ),
                            "ir_background_input_png": str(
                                background_path.relative_to(out_dir)
                            ),
                            "ir_wav": str(aligned_wav.relative_to(out_dir))
                            if not args.skip_audio else None,
                            "ir_scrambled_wav": str(scrambled_wav.relative_to(out_dir))
                            if not args.skip_audio else None,
                            "ir_background_wav": str(background_wav.relative_to(out_dir))
                            if not args.skip_audio else None,
                        })
                        trial_number += 1
                layout_number += 1

    if not args.skip_audio:
        normalize_family_wav_rms(stimuli, out_dir)

    manifest = {
        "schema_version": 8,
        "task": "ir-ishihara-ambiguous-metamers",
        "seed": args.seed,
        "plate_width": PLATE_W,
        "plate_height": PLATE_H,
        "soundscape_width": IMG_W,
        "soundscape_height": IMG_H,
        "visual_to_audio_scale_x": PLATE_W / IMG_W,
        "visual_to_audio_scale_y": PLATE_H / IMG_H,
        "coordinate_mapping": "full-frame-normalized-no-crop",
        "ir_resampling": "lanczos-geometry-score-with-histogram-matched-intensity",
        "soundscape_duration_ms": round(WAV_TOTAL_TIME_S * 1000),
        "soundscape_sample_rate_hz": WAV_SAMPLE_FREQ_HZ,
        "soundscape_sample_count": WAV_SAMPLE_COUNT,
        "soundscape_samples_per_column": WAV_SAMPLES_PER_COLUMN,
        "soundscape_uses_bspline": True,
        "variants_per_glyph": args.variants_per_glyph,
        "layout_exemplars_per_family": args.variants_per_glyph,
        "audio_generated": not args.skip_audio,
        "audio_rms_normalized_within_family": not args.skip_audio,
        "nonprobe_audio": "matched-nonzero-ir-background-carrier",
        "background": "neutral-grey",
        "diagnostic_invariant": (
            "The retained RGB scaffold is exactly the decoy response glyph. "
            "The final probe component transforms it into the target response "
            "glyph. Every response set includes the decoy plus several matched "
            "probe interpretations, so completion count does not identify the target."
        ),
        "complexity_tiers": {
            str(level): {
                "family_id": tier["family_id"],
                "label": tier["label"],
                "description": tier["description"],
                "glyphs": list(tier["glyphs"]),
            }
            for level, tier in COMPLEXITY_TIERS.items()
        },
        "metamer_families": {
            family["family_id"]: {
                "complexity_level": level,
                "label": family["label"],
                "description": family["description"],
                "transformations": list(family["transformations"]),
            }
            for level, family in METAMER_FAMILIES.items()
        },
        "channel_recipes": {
            recipe_id: {
                "label": recipe["label"],
                "visible_channels": list(recipe["visible_channels"]),
                "crossmodal_channels": list(recipe["crossmodal_channels"]),
            }
            for recipe_id, recipe in CHANNEL_RECIPES.items()
        },
        "curriculum_recipe_by_complexity": {
            str(level): recipe_id
            for level, recipe_id in CURRICULUM_RECIPE_BY_COMPLEXITY.items()
        },
        "glyph_thumbnails": {
            choice_id: f"glyphs/{choice_id}.png"
            for choice_id in ALL_RESPONSE_CHOICES
        },
        "stimuli": stimuli,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(stimuli)} paired Ishihara stimuli to {out_dir}")
    return manifest_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--variants-per-glyph", type=int, default=3)
    parser.add_argument(
        "--out", type=Path,
        default=ROOT / "ishihara_stimuli",
    )
    parser.add_argument(
        "--raspivoice-bin", type=Path, default=DEFAULT_RASPIVOICE_BIN,
        help="path to the raspivoice executable (or set RASPIVOICE_BIN)",
    )
    parser.add_argument(
        "--skip-audio", action="store_true",
        help="generate RGB/IR image assets and a manifest without invoking raspivoice",
    )
    args = parser.parse_args()
    if args.variants_per_glyph < 1:
        parser.error("--variants-per-glyph must be at least 1")
    generate(args)


if __name__ == "__main__":
    main()
