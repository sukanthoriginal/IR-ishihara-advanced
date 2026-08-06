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

Output folder is named <cols>x<rows> (width x height, like a resolution) --
e.g. --rows 9 --cols 16 writes to stimuli/16x9/.
"""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

RASPIVOICE_BIN = Path(__file__).parent.parent / "IR-vOICe" / "raspivoice" / "Release" / "raspivoice"

# raspivoice free-runs its main loop as fast as possible (hundreds of
# frames/sec) and forks `aplay` on every frame. On Mac, aplay doesn't exist,
# so that's a failed fork+exec+shell-PATH-search hundreds of times per
# stimulus -- across a large bank that's enough process-spawn overhead to
# occasionally stall the scheduler past our polling window. A no-op shim
# ahead of PATH turns that into an instant no-op fork+exec instead.
SHIM_DIR = Path(__file__).parent / ".aplay_shim"


def ensure_aplay_shim():
    SHIM_DIR.mkdir(exist_ok=True)
    shim = SHIM_DIR / "aplay"
    if not shim.exists():
        shim.write_text("#!/bin/sh\nexit 0\n")
        shim.chmod(0o755)

# Matches raspivoice's --columns / --rows defaults -- the algorithm resizes
# any input image to this resolution anyway, so generate at 1:1 to avoid an
# extra resize/interpolation step.
IMG_W, IMG_H = 178, 64

BACKGROUND_LEVEL = 25       # dim, not pure black -- real IR frames have a floor
BACKGROUND_NOISE_STD = 5.0
BLOB_PEAK = 255
BLOB_RADIUS_FRAC = 0.35     # fraction of min(cell_w, cell_h)

# Blob center is randomly offset from the cell's exact midpoint, up to this
# fraction of the cell's half-width/half-height in each axis. Keeps the task
# from being artificially easy (target always dead-center) and gives L2 error
# a real precision signal to measure, while staying capped well short of 1.0
# so the blob never becomes ambiguous with a neighboring cell.
DEFAULT_JITTER_FRAC = 0.5

RASPIVOICE_MAX_WAIT_S = 6.0   # raspivoice loops forever re-reading the file,
                               # rewriting it whole on every frame (every
                               # ~5-10ms) -- we poll for the WAV to appear
                               # rather than sleeping blind, since process
                               # scheduling latency varies with system load.
RASPIVOICE_POLL_INTERVAL_S = 0.02

# raspivoice defaults (explicitly pinned via CLI flags below so this stays
# correct even if the binary's defaults ever change). Duration and format are
# fixed, so every correctly-written WAV must be exactly this many bytes --
# anything else means we killed the process mid-write of a frame (it
# rewrites the whole file every ~5-10ms) and got a torn/truncated file.
WAV_TOTAL_TIME_S = 1.05
WAV_SAMPLE_FREQ_HZ = 48000
WAV_CHANNELS = 2
WAV_BYTES_PER_SAMPLE = 2
WAV_HEADER_BYTES = 44
EXPECTED_WAV_BYTES = (
    WAV_HEADER_BYTES
    + round(WAV_TOTAL_TIME_S * WAV_SAMPLE_FREQ_HZ) * WAV_CHANNELS * WAV_BYTES_PER_SAMPLE
)


def synthesize_cell_image(row, col, grid_rows, grid_cols, rng, jitter_frac):
    cell_w = IMG_W / grid_cols
    cell_h = IMG_H / grid_rows
    center_x = (col + 0.5) * cell_w
    center_y = (row + 0.5) * cell_h
    target_x = center_x + rng.uniform(-jitter_frac, jitter_frac) * (cell_w / 2)
    target_y = center_y + rng.uniform(-jitter_frac, jitter_frac) * (cell_h / 2)

    background = rng.normal(BACKGROUND_LEVEL, BACKGROUND_NOISE_STD, (IMG_H, IMG_W))

    yy, xx = np.mgrid[0:IMG_H, 0:IMG_W]
    sigma = BLOB_RADIUS_FRAC * min(cell_w, cell_h)
    dist_sq = (xx - target_x) ** 2 + (yy - target_y) ** 2
    blob = BLOB_PEAK * np.exp(-dist_sq / (2 * sigma ** 2))

    img = np.clip(background + blob, 0, 255).astype(np.uint8)
    return img, round(target_x), round(target_y)


def run_raspivoice(png_path, wav_path, retries=3):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            _run_raspivoice_once(png_path, wav_path)
            return
        except RuntimeError as e:
            last_error = e
            print(f"  retry {attempt}/{retries} after: {e}")
    raise last_error


def _run_raspivoice_once(png_path, wav_path):
    wav_path.unlink(missing_ok=True)  # don't let a previous attempt's file fool the check

    env = os.environ.copy()
    env["PATH"] = f"{SHIM_DIR}:{env.get('PATH', '')}"
    proc = subprocess.Popen(
        [
            str(RASPIVOICE_BIN),
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
    # Poll for the file to land at exactly the right size, then kill
    # immediately -- no artificial "settle" delay. A delay only widens the
    # window for the next frame's rewrite to land mid-kill; checking the
    # exact expected size and killing right away minimizes it instead.
    deadline = time.monotonic() + RASPIVOICE_MAX_WAIT_S
    while time.monotonic() < deadline:
        if wav_path.exists() and wav_path.stat().st_size == EXPECTED_WAV_BYTES:
            break
        time.sleep(RASPIVOICE_POLL_INTERVAL_S)

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    # Final check against the process's own dying gasp: re-stat after the
    # process is fully dead, since terminate() itself could race a rewrite.
    if not wav_path.exists() or wav_path.stat().st_size != EXPECTED_WAV_BYTES:
        actual = wav_path.stat().st_size if wav_path.exists() else "missing"
        raise RuntimeError(f"raspivoice wrote a bad WAV for {png_path} "
                            f"(expected {EXPECTED_WAV_BYTES} bytes, got {actual})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, required=True, help="click-grid rows")
    parser.add_argument("--cols", type=int, required=True, help="click-grid cols")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--jitter", type=float, default=DEFAULT_JITTER_FRAC,
        help="random blob offset from cell center, as a fraction of cell "
             "half-width/half-height (0.0 = always dead-center)",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="output dir (default: stimuli/<cols>x<rows>/)",
    )
    args = parser.parse_args()

    ensure_aplay_shim()

    if not RASPIVOICE_BIN.exists():
        raise SystemExit(
            f"raspivoice binary not found at {RASPIVOICE_BIN}. "
            "Build it first (see ../IR-vOICe/raspivoice/release_mac.mak)."
        )

    out_dir = args.out or Path(__file__).parent / "stimuli" / f"{args.cols}x{args.rows}"
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    cells = []

    for row in range(args.rows):
        for col in range(args.cols):
            cell_index = row * args.cols + col
            name = f"cell_{row:02d}_{col:02d}"
            png_path = out_dir / f"{name}.png"
            wav_path = out_dir / f"{name}.wav"

            img, target_x, target_y = synthesize_cell_image(row, col, args.rows, args.cols, rng, args.jitter)
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
