# Supervised-learning simulator — build plan

Context: [`supervised-learning-simulator.md`](./supervised-learning-simulator.md) (meeting notes, 2026-08-05
with Sushrut Thorat). Companion hardware repo: `../IR-vOICe`.

Core constraint driving everything below: the self-supervised (real-world IR-vOICe) vs.
supervised (simulator) comparison only means something if both arms learn the *same*
sound-to-space mapping. So the simulator generates stimuli by reusing the real
`raspivoice` soundscape code (`ImageToSoundscape.cpp`, `AudioData.cpp`) as-is — never a
reimplementation. Confirmed: build and run it locally on Mac (not the Pi) for fast
iteration, since the audio-generation code path has no platform-specific dependencies.

---

## Phase 1 — Port the core engine to Mac, validate it matches the Pi

`raspivoice` already supports still-image input: `-s0 -i target.png -o target.wav`
(see `raspivoice/Options.cpp`, `RaspiVoice.cpp`) — no camera needed. The blocker to
building this on Mac is `KeyboardInput.cpp` / `rotaryencoder.cpp`, which hard-depend on
`wiringPi` (Broadcom GPIO, Linux-only) and `<linux/input.h>` (evdev, Linux-only) for the
rotary-encoder knob and raw-keyboard-grab input modes — unrelated to the stimulus-
generation path, but currently compiled in unconditionally.

**Steps:**
1. Add a build-time guard (mirror the existing `NO_RASPICAM` pattern) — call it
   `NO_WIRINGPI` — that stubs out `InputType::RotaryEncoder` and the evdev keyboard-grab
   path in `KeyboardInput.cpp`. Keep the plain ncurses/terminal input types (ncurses is
   portable, available via Homebrew).
2. Write `release_mac.mak`: `pkg-config opencv4` against Homebrew's OpenCV, drop
   `wiringPi` from `LIBRARY_NAMES`, drop the Pi-specific arch flags.
3. `brew install opencv ncurses` (or confirm already present), build, confirm
   `./Release/raspivoice --help` runs.
4. **Validation:** generate one test image (simple bright blob on black), run
   `-s0 -i test.png -o test_mac.wav` on both Mac and Pi, compare the two WAVs
   (byte-diff if possible, else spectrogram/listen A-B). If Mac is Apple Silicon, expect
   near-bit-identical output (same aarch64 arch as the Pi); if Intel, expect at most
   inaudible floating-point drift in the oscillator math — confirm before trusting the
   pipeline for real data.

Exit criterion: Mac-built binary produces a soundscape indistinguishable from the Pi's
for the same input image.

---

## Phase 2 — Stimulus bank generator

A script (Python, matching the `ir-cam.py` precedent) that, for a given grid config
(rows × cols):
- Synthesizes one image per grid cell: bright blob centered in that cell on a dark
  background, sized/contrasted to resemble a real IR-hot object against a cold scene
  (reference `images/with-IR-source-light.png` for what a real "hot" blob looks like
  post-pipeline).
- Runs each through `raspivoice -s0 -i cell_NN.png -o cell_NN.wav`.
- Caches everything under `stimuli/<rows>x<cols>/`, plus a `manifest.json` mapping
  cell index → target pixel-center coords (for L2 scoring later) → wav filename.

Open parameters to tune empirically once Phase 1 is validated: blob size relative to
cell size, background floor (real IR frames aren't pure black), image resolution vs.
`--rows`/`--columns` the algorithm expects. Cheap to regenerate, so iterate freely.

Exit criterion: full stimulus bank for a 3×3 grid (9 WAVs + manifest), spot-checked by
ear that distinct cells sound distinguishable.

---

## Phase 3 — Browser front-end MVP (the actual psychophysics loop)

Plain web page, no build step to start — HTML canvas/DOM grid + Web Audio API. Consider
scaffolding trial sequencing/timing/data-export with `jsPsych` rather than hand-rolling.
Served locally (`python3 -m http.server` or similar) pointing at the stimulus bank +
page — no backend needed for the MVP.

**Trial loop:**
1. Draw the clickable grid for the current config.
2. Pick a random cell from the manifest, play its cached WAV.
3. Capture the click: coords + `event.timeStamp`, compute RT from audio-start.
4. Score: nearest-grid-cell match → correct/incorrect; raw pixel distance → L2 error
   (keep continuous, don't discretize, so spatial-resolution-at-threshold can be
   estimated later without rerunning at finer grids).
5. Append trial row to an in-memory log; append fields: participant_id, arm
   (self-supervised / supervised / novice), grid_rows, grid_cols, target_cell,
   target_x_px, target_y_px, click_x_px, click_y_px, correct, rt_ms, l2_error_px,
   timestamp.
6. At block end, export the log (download CSV/JSON).

**MVP scope:** single grid size (3×3), single target per trial, one fixed-length block
(e.g. 20–30 trials), minimal instructions screen. No progression/staircase logic yet —
get the full loop (image → sound → click → score → log) proven end-to-end first.

Exit criterion: you can run a real block of trials on your own monitor and get a clean
CSV out.

---

## Phase 4 — Progression across grid sizes (after Phase 3 is proven)

- Add more stimulus banks: 4×3, 16×9, ... (Phase 2 script, different grid configs).
- Decide block-based (fixed N trials per resolution, then advance) vs. staircase
  (advance resolution only after clearing an accuracy threshold) — staircase gets to
  the "finest discriminable grid" metric faster but is more logic. Lean toward starting
  block-based since it's simpler and the data is still analyzable either way; revisit if
  session length becomes a problem.
- This is also the point to stand up the novice-baseline arm (send the URL to Lossfunk
  people) and start tracking sessions/participants distinctly in the log schema.

---

## Later / not in scope yet

- Alphabet / number / image stimuli (bigger lift — revisit once the grid task produces
  sane learning-curve data).
- Sleep-consolidation training — explicitly deferred per the meeting notes.
- Neuroimaging (fMRI/EEG/MEG) — gated on simulator data showing performance at or beyond
  regular human visual baselines; not started until then.

---

## Immediate next step

Phase 1, step 1: add the `NO_WIRINGPI` guard to `KeyboardInput.cpp`/`rotaryencoder.cpp`
in `../IR-vOICe/raspivoice/`.
