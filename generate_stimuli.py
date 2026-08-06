"""
Phase 2 stimulus bank generator.

For a given click-grid size (rows x cols), synthesizes one bright-blob-on-dim-
background image per grid cell (matching what a real IR-hot object looks like
against a cold scene, c.f. ../IR-vOICe/images/with-IR-source-light.png), runs
each through the real raspivoice binary to get the exact soundscape a
participant would hear, and writes a manifest mapping cell -> target pixel
coords -> wav filename.

Reuses the real vOICe algorithm as-is (see simulator-plan.md) -- this script
only produces input images, it does not touch the sound-generation code path.

Usage:
    python3 generate_stimuli.py --rows 3 --cols 3
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

RASPIVOICE_BIN = Path(__file__).parent.parent / "IR-vOICe" / "raspivoice" / "Release" / "raspivoice"

# Matches raspivoice's --columns / --rows defaults -- the algorithm resizes
# any input image to this resolution anyway, so generate at 1:1 to avoid an
# extra resize/interpolation step.
IMG_W, IMG_H = 178, 64

BACKGROUND_LEVEL = 25       # dim, not pure black -- real IR frames have a floor
BACKGROUND_NOISE_STD = 5.0
BLOB_PEAK = 255
BLOB_RADIUS_FRAC = 0.35     # fraction of min(cell_w, cell_h)

RASPIVOICE_TIMEOUT_S = 1.5  # raspivoice loops forever re-reading the file;
                            # kill it after this long -- one frame cycle takes
                            # a few ms, so this guarantees a complete WAV.


def synthesize_cell_image(row, col, grid_rows, grid_cols, rng):
    cell_w = IMG_W / grid_cols
    cell_h = IMG_H / grid_rows
    target_x = (col + 0.5) * cell_w
    target_y = (row + 0.5) * cell_h

    background = rng.normal(BACKGROUND_LEVEL, BACKGROUND_NOISE_STD, (IMG_H, IMG_W))

    yy, xx = np.mgrid[0:IMG_H, 0:IMG_W]
    sigma = BLOB_RADIUS_FRAC * min(cell_w, cell_h)
    dist_sq = (xx - target_x) ** 2 + (yy - target_y) ** 2
    blob = BLOB_PEAK * np.exp(-dist_sq / (2 * sigma ** 2))

    img = np.clip(background + blob, 0, 255).astype(np.uint8)
    return img, round(target_x), round(target_y)


def run_raspivoice(png_path, wav_path):
    proc = subprocess.Popen(
        [
            str(RASPIVOICE_BIN),
            "-s0",
            "-i", str(png_path),
            "-o", str(wav_path),
            "-r", str(IMG_H),
            "-c", str(IMG_W),
            "--no_record",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(RASPIVOICE_TIMEOUT_S)
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    if not wav_path.exists() or wav_path.stat().st_size == 0:
        raise RuntimeError(f"raspivoice did not produce a WAV for {png_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, required=True, help="click-grid rows")
    parser.add_argument("--cols", type=int, required=True, help="click-grid cols")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--out", type=Path, default=None,
        help="output dir (default: stimuli/<rows>x<cols>/)",
    )
    args = parser.parse_args()

    if not RASPIVOICE_BIN.exists():
        raise SystemExit(
            f"raspivoice binary not found at {RASPIVOICE_BIN}. "
            "Build it first (see ../IR-vOICe/raspivoice/release_mac.mak)."
        )

    out_dir = args.out or Path(__file__).parent / "stimuli" / f"{args.rows}x{args.cols}"
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    cells = []

    for row in range(args.rows):
        for col in range(args.cols):
            cell_index = row * args.cols + col
            name = f"cell_{row:02d}_{col:02d}"
            png_path = out_dir / f"{name}.png"
            wav_path = out_dir / f"{name}.wav"

            img, target_x, target_y = synthesize_cell_image(row, col, args.rows, args.cols, rng)
            Image.fromarray(img, mode="L").save(png_path)

            print(f"[{cell_index + 1}/{args.rows * args.cols}] {name}: "
                  f"target=({target_x},{target_y}) -> running raspivoice...")
            run_raspivoice(png_path, wav_path)

            cells.append({
                "cell_index": cell_index,
                "row": row,
                "col": col,
                "target_x_px": target_x,
                "target_y_px": target_y,
                "png": png_path.name,
                "wav": wav_path.name,
            })

    manifest = {
        "grid_rows": args.rows,
        "grid_cols": args.cols,
        "image_width": IMG_W,
        "image_height": IMG_H,
        "cells": cells,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote {len(cells)} stimuli + manifest to {out_dir}")


if __name__ == "__main__":
    main()
