#!/usr/bin/env python3
"""Generate paired visible-colour and IR-defined Ishihara stimuli.

Every stimulus starts from one dot layout and one glyph mask.  The generator
then emits three spatially registered views:

* ``visible``: target dots are green and background dots are red.
* ``ir_hidden``: every dot is drawn from the same red palette, so RGB carries
  no information about glyph membership.
* ``ir_input``: target dots are IR-bright and background dots are IR-dim.

``ir_input`` is passed through the repository's real raspivoice binary, just
like the localization task, rather than through a browser reimplementation.
A second IR file keeps the same number and energy of bright dots but shuffles
their locations, providing a spatial-scramble control.

Usage:
    python3 generate_ishihara_stimuli.py
    python3 generate_ishihara_stimuli.py --skip-audio  # asset/UI development
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import generate_stimuli as localization_generator


IMG_H = localization_generator.IMG_H
IMG_W = localization_generator.IMG_W
WAV_TOTAL_TIME_S = localization_generator.WAV_TOTAL_TIME_S
DEFAULT_RASPIVOICE_BIN = Path(
    os.environ.get("RASPIVOICE_BIN", localization_generator.RASPIVOICE_BIN)
)


PLATE_SCALE = 4
PLATE_W = IMG_W * PLATE_SCALE
PLATE_H = IMG_H * PLATE_SCALE
DOT_STEP = 16

TRAIN_GLYPHS = ("star", "triangle", "crescent", "lightning")
TEST_GLYPHS = ("chevron", "hourglass", "fork", "spiral")
ALL_GLYPHS = TRAIN_GLYPHS + TEST_GLYPHS


def _normal_point(x: float, y: float) -> tuple[int, int]:
    """Map normalized glyph coordinates onto the 178x64 soundscape frame."""
    return round(x * IMG_W), round(y * IMG_H)


def make_glyph_mask(glyph_id: str) -> Image.Image:
    """Return a thick, centered binary glyph mask at raspivoice resolution."""
    image = Image.new("L", (IMG_W, IMG_H), 0)
    draw = ImageDraw.Draw(image)
    width = 5

    if glyph_id == "star":
        cx, cy = _normal_point(0.5, 0.5)
        outer, inner = 25, 11
        points = []
        for i in range(10):
            angle = -math.pi / 2 + i * math.pi / 5
            radius = outer if i % 2 == 0 else inner
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        draw.polygon(points, fill=255)
    elif glyph_id == "triangle":
        draw.line(
            [_normal_point(0.5, 0.12), _normal_point(0.31, 0.82),
             _normal_point(0.69, 0.82), _normal_point(0.5, 0.12)],
            fill=255, width=width, joint="curve",
        )
    elif glyph_id == "crescent":
        draw.ellipse([_normal_point(0.35, 0.12), _normal_point(0.66, 0.88)], fill=255)
        draw.ellipse([_normal_point(0.45, 0.08), _normal_point(0.72, 0.76)], fill=0)
    elif glyph_id == "lightning":
        draw.polygon([
            _normal_point(0.49, 0.08), _normal_point(0.34, 0.53),
            _normal_point(0.48, 0.53), _normal_point(0.40, 0.92),
            _normal_point(0.68, 0.40), _normal_point(0.54, 0.40),
        ], fill=255)
    elif glyph_id == "chevron":
        draw.line(
            [_normal_point(0.31, 0.25), _normal_point(0.50, 0.72),
             _normal_point(0.69, 0.25)],
            fill=255, width=width + 2, joint="curve",
        )
    elif glyph_id == "hourglass":
        draw.line(
            [_normal_point(0.33, 0.16), _normal_point(0.67, 0.16),
             _normal_point(0.36, 0.84), _normal_point(0.64, 0.84),
             _normal_point(0.33, 0.16)],
            fill=255, width=width, joint="curve",
        )
    elif glyph_id == "fork":
        draw.line(
            [_normal_point(0.34, 0.18), _normal_point(0.50, 0.48),
             _normal_point(0.66, 0.18)],
            fill=255, width=width + 1, joint="curve",
        )
        draw.line(
            [_normal_point(0.50, 0.48), _normal_point(0.50, 0.88)],
            fill=255, width=width + 1,
        )
    elif glyph_id == "spiral":
        cx, cy = _normal_point(0.5, 0.5)
        points = []
        for i in range(90):
            theta = i / 89 * math.pi * 4.2
            radius = 2 + i / 89 * 24
            points.append((cx + radius * math.cos(theta), cy + 0.72 * radius * math.sin(theta)))
        draw.line(points, fill=255, width=width, joint="curve")
    else:
        raise ValueError(f"unknown glyph: {glyph_id}")

    return image


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


def draw_trial_assets(glyph_id: str, rng: np.random.Generator):
    low_mask = make_glyph_mask(glyph_id)
    high_mask = low_mask.resize((PLATE_W, PLATE_H), Image.Resampling.NEAREST)
    dots = make_dot_layout(rng)
    target_flags = [high_mask.getpixel((x, y)) > 0 for x, y, _ in dots]

    # Thin line glyphs (notably the held-out fork) intentionally occupy less
    # area than filled glyphs. Two dozen sampled dots is still dense enough to
    # preserve their topology after the 178x64 IR downsample.
    if sum(target_flags) < 24:
        raise RuntimeError(f"glyph {glyph_id} produced too few target dots")

    visible = Image.new("RGB", (PLATE_W, PLATE_H), (26, 24, 23))
    ir_hidden = Image.new("RGB", (PLATE_W, PLATE_H), (26, 24, 23))
    visible_draw = ImageDraw.Draw(visible)
    hidden_draw = ImageDraw.Draw(ir_hidden)

    # Approximate luminance matching keeps the visible benchmark about hue-led
    # rather than making the target trivially pop through brightness alone.
    red = (181, 72, 75)
    green = (65, 110, 70)
    hidden_reds = ((174, 70, 68), (159, 79, 70), (188, 68, 76), (166, 73, 82))

    for (x, y, radius), is_target in zip(dots, target_flags):
        box = (x - radius, y - radius, x + radius, y + radius)
        visible_draw.ellipse(box, fill=_vary_colour(green if is_target else red, rng))
        # The same colour distribution is sampled independently of target
        # membership, so RGB cannot reveal the IR-defined figure.
        hidden_base = hidden_reds[int(rng.integers(0, len(hidden_reds)))]
        hidden_draw.ellipse(box, fill=_vary_colour(hidden_base, rng))

    aligned_ir = _draw_ir_map(dots, target_flags, rng)
    scrambled_ir = _scramble_ir_map(aligned_ir, rng)

    return visible, ir_hidden, aligned_ir, scrambled_ir


def _draw_ir_map(dots, bright_flags, rng: np.random.Generator) -> Image.Image:
    noise = rng.normal(16, 2.5, (PLATE_H, PLATE_W))
    high = Image.fromarray(np.clip(noise, 0, 255).astype(np.uint8), mode="L")
    draw = ImageDraw.Draw(high)
    for (x, y, radius), is_bright in zip(dots, bright_flags):
        level = int(rng.integers(232, 256)) if is_bright else int(rng.integers(20, 33))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=level)
    return high.resize((IMG_W, IMG_H), Image.Resampling.LANCZOS)


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


def generate(args) -> Path:
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "glyphs").mkdir(exist_ok=True)

    if not args.skip_audio:
        localization_generator.ensure_aplay_shim()
        raspivoice_bin = args.raspivoice_bin
        if not raspivoice_bin.exists():
            raise SystemExit(
                f"raspivoice binary not found at {raspivoice_bin}. "
                "Pass --raspivoice-bin, set RASPIVOICE_BIN, build the companion "
                "IR-vOICe repository, or use --skip-audio."
            )
        if not os.access(raspivoice_bin, os.X_OK):
            raise SystemExit(f"raspivoice binary is not executable: {raspivoice_bin}")
        # The shared runner resolves its executable from its module-level
        # constant. Point it at the explicitly selected binary for this run.
        localization_generator.RASPIVOICE_BIN = raspivoice_bin

    for glyph_id in ALL_GLYPHS:
        make_glyph_mask(glyph_id).save(out_dir / "glyphs" / f"{glyph_id}.png")

    stimuli = []
    trial_number = 0
    for split, glyphs in (("train", TRAIN_GLYPHS), ("test", TEST_GLYPHS)):
        for glyph_id in glyphs:
            for variant in range(args.variants_per_glyph):
                seed = args.seed + trial_number * 1009
                rng = np.random.default_rng(seed)
                stem = f"{split}_{glyph_id}_{variant:02d}"
                trial_dir = out_dir / stem
                trial_dir.mkdir(exist_ok=True)

                visible, hidden, aligned_ir, scrambled_ir = draw_trial_assets(glyph_id, rng)
                visible_path = trial_dir / "visible.png"
                hidden_path = trial_dir / "ir_hidden.png"
                aligned_path = trial_dir / "ir_input.png"
                scrambled_path = trial_dir / "ir_scrambled_input.png"
                visible.save(visible_path)
                hidden.save(hidden_path)
                aligned_ir.save(aligned_path)
                scrambled_ir.save(scrambled_path)

                aligned_wav = trial_dir / "ir.wav"
                scrambled_wav = trial_dir / "ir_scrambled.wav"
                if not args.skip_audio:
                    print(f"[{trial_number + 1}] {stem}: generating aligned audio")
                    localization_generator.run_raspivoice(aligned_path, aligned_wav)
                    print(f"[{trial_number + 1}] {stem}: generating scrambled control")
                    localization_generator.run_raspivoice(scrambled_path, scrambled_wav)

                stimuli.append({
                    "stimulus_id": stem,
                    "split": split,
                    "glyph_id": glyph_id,
                    "seed": seed,
                    "visible_png": str(visible_path.relative_to(out_dir)),
                    "ir_hidden_png": str(hidden_path.relative_to(out_dir)),
                    "ir_input_png": str(aligned_path.relative_to(out_dir)),
                    "ir_scrambled_input_png": str(scrambled_path.relative_to(out_dir)),
                    "ir_wav": str(aligned_wav.relative_to(out_dir)) if not args.skip_audio else None,
                    "ir_scrambled_wav": str(scrambled_wav.relative_to(out_dir)) if not args.skip_audio else None,
                })
                trial_number += 1

    manifest = {
        "schema_version": 1,
        "task": "ir-ishihara-role-substitution",
        "seed": args.seed,
        "plate_width": PLATE_W,
        "plate_height": PLATE_H,
        "soundscape_width": IMG_W,
        "soundscape_height": IMG_H,
        "soundscape_duration_ms": round(WAV_TOTAL_TIME_S * 1000),
        "variants_per_glyph": args.variants_per_glyph,
        "audio_generated": not args.skip_audio,
        "glyphs": {
            "train": list(TRAIN_GLYPHS),
            "test": list(TEST_GLYPHS),
        },
        "glyph_thumbnails": {glyph: f"glyphs/{glyph}.png" for glyph in ALL_GLYPHS},
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
        default=Path(__file__).parent / "ishihara_stimuli",
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
