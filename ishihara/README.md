# IR ambiguity-grammar simulator

This is the controlled experimental counterpart to the repository's
[live IR-vOICe simulator](../README.md). The live simulator answers “what is
it like to use the IR stream continuously?” This simulator asks the narrower,
measurable question: **does the participant use an IR-audio component to resolve
a shape as accurately and quickly as a matched visible-colour component?**

## The system in one sentence

A static RGB dot pattern deliberately supports a plausible wrong answer, while
one additional shape component—shown as an ordinary visible colour in the
comparator condition or heard through `raspivoice` in the IR condition—changes
that pattern into the target answer.

Formally, let `S` be the retained visible scaffold and `P` the diagnostic probe:

```text
S     = coherent decoy
S + P = target
```

The response screen always contains the target, the decoy, and two structurally
matched alternatives. A participant who does not recover `P` should therefore
be drawn toward a meaningful decoy rather than reduced to an arbitrary guess.

## What happens in one trial

1. The participant looks at a centre crosshair and initiates the trial using
   the selected response device: any ordinary key for keyboard blocks, or a
   centre-crosshair click for pointer blocks.
2. The complete RGB scaffold appears statically. It never sweeps visually.
3. Every trial plays three 1.05-second left-to-right vOICe soundscapes,
   separated by two 250 ms silent intervals. In an IR condition, the auditory
   carrier contains the bright spatial probe. In the matched visible condition,
   the same probe is visible while the audio contains only the nonzero dark-IR
   background texture. Both presentations last 3.65 seconds.
4. A 220 ms mask replaces the plate.
5. Four confusable glyph interpretations appear. The participant responds with
   keys 1–4 (recommended) or a pointer click.
6. The task records target accuracy, decoy capture, response time, condition,
   stimulus identity, presentation timing, display geometry, and interruption
   audit fields.

The default mixed block presents every transformation once with a visible probe
and once with an IR probe. The paired versions use the same scaffold, dot layout,
probe geometry, and response set, and are placed in opposite block halves with
counterbalanced order.

## Experimental conditions

| Condition | Static visual plate | Audio | Intended comparison |
| --- | --- | --- | --- |
| `mixed` | Schedules matched `visual-composite` and `ir-composite` trials | Condition-dependent | Primary within-stimulus visible-versus-IR comparison |
| `visual-composite` | Scaffold plus visibly coloured probe | Background-only IR carrier, three sweeps | Upper comparator: `S + P` is fully visual without a sound-versus-silence cue |
| `ir-composite` | Scaffold only | Aligned IR probe, three sweeps | Test: vision supplies `S`, audio supplies `P` |
| `visible-only` | Scaffold only | Background-only IR carrier, three sweeps | Measures coherent decoy capture when `P` is absent |
| `ir-only` | Neutral dot plate | Aligned IR probe, three sweeps | Tests whether the probe can be decoded without the visual scaffold |
| `ir-scrambled` | Scaffold only | Spatially scrambled, energy-matched IR probe | Tests whether performance depends on probe geometry rather than audio energy |

## Why it is an ambiguity-grammar task

This experiment makes one colour or IR component decisively necessary for the
response without making every trial obey the same completion rule. Every
retained-RGB scaffold is a coherent decoy. The final probe channel resolves one
of several matched interpretations, and every four-choice set contains the
decoy plus three structurally informative alternatives.

Shape complexity and channel count are independent experimental factors:

- **Level 1 — two-bit factorial pairs:** the choices cross two possible edits,
  such as CI/CT/GT/GI, and the target is not always the both-edited state.
- **Level 2 — alternative completion forks:** an identical F scaffold can
  become E, P, or R depending only on probe geometry.
- **Level 3 — multi-stroke factorials:** pairs such as P3/B3/P8/B8 expose
  partial recovery of coordinated edits.
- **Level 4 — three-way chimeric branches:** one of three positions changes,
  producing three equally completed alternatives plus the intact decoy.
- **Channel recipes:** R+IR, G+IR, R+G+IR, and R+G+B+IR.

The scaffold is divided across every non-probe channel. The diagnostic feature
is visible only in the all-visible comparator and entirely IR in the crossmodal
version. The paired recipes are R+G versus R+IR, G+B versus G+IR, R+G+B versus
R+G+IR, and R+G+B+Y versus R+G+B+IR.

The central invariant is machine-tested: the scaffold mask exactly matches the
decoy response thumbnail, the scaffold plus probe exactly matches the target,
and both are always offered. A probe-blind observer can therefore show high
**decoy capture**, while factorial foils reveal partial integration. Completion
forks and chimeras deliberately provide several plausible “something was added”
answers, blocking the earlier most-complete-option shortcut.

Probe-dot count and radius are equal within each complexity level, aligned IR
maps share an exact intensity histogram, and generated WAV RMS is normalized
within each four-transformation group. Every stimulus also has a matched
background-only carrier: its nonzero dark pixels are identical to the aligned
IR input outside the bright probe. This removes silence as a condition cue and
forces the listener to separate coherent probe geometry from sensor texture.
These controls block dot count, image energy, and whole-file loudness as target
shortcuts. IR-only and spatially scrambled-IR controls
separately test probe decoding and spatial integration. The CSV records target
accuracy, decoy selection, and the exact four-choice response set. The task
measures use of channel-specific information; it does not establish a new
colour quale.

## What a positive result would mean

The primary evidence is not simply above-chance performance. The informative
pattern is high and improving `ir-composite` accuracy, lower decoy capture, and
competitive response time relative to the paired `visual-composite` trials,
combined with poorer performance in `visible-only` and `ir-scrambled` controls.
Generalisation from familiar to held-out dot layouts would further argue against
memorisation of individual plates.

Such a result would support the claim that the trained participant can integrate
spatial information carried by IR audio with a simultaneous visual scaffold to
perform colour-like compositional discrimination. By itself it would not show a
novel colour quale, identify a neural mechanism, or demonstrate cortical
rewiring; those require separate phenomenological and neuroimaging evidence.

## Static RGB with swept audio

The complete RGB plate is presented statically; there is no visual sweep. In
every condition, only the `raspivoice` soundscape scans left-to-right.
The static plate appears at the first audio onset, stays visible across all
three audio sweeps and their two 250 ms silent intervals, and is then masked.
Visual-only conditions use the matched background carrier and the same total
3.65-second exposure duration.

All three audio buffers are scheduled in advance on one Web Audio timeline, so
timing error cannot accumulate between sweeps. The CSV records planned and
completed audio sweeps, audio sweep geometry, static visual duration, and the
static plate's browser-observed onset offset.

With the generated 48 kHz, 1.05 s, 178-column soundscape, `raspivoice` assigns
283 samples (5.896 ms) to each scan column. An interior pixel contributes over
three adjacent column slices, for 17.688 ms of nonzero audio support. These
B-spline figures describe only the audio encoding; RGB information is not
revealed column-by-column.

The full visual raster is 712×256 and the audio raster is 178×64: an exact 4:1
coordinate scale on both axes with the same 178:64 aspect ratio. The IR map is
therefore a full-field downsample, not a crop or a resize to the browser window.
Probe geometry is downsampled with a Lanczos score before a fixed intensity
histogram is assigned; this preserves the coordinate topology while matching
IR pixel-energy statistics across alternatives. `raspivoice` is explicitly
invoked with `-c 178 -r 64`, so it does not perform another spatial resize.

Each complexity level contains four distinct decoy-to-target transformations,
so a block no longer repeats one positional template. Familiar and held-out
blocks use different seeded dot layouts for the same transformations, allowing
layout transfer without changing response-set difficulty.
The default 32-trial mixed block presents all 16 transformations once with a
visible probe and once with an IR probe, separated across opposite block halves.
The browser masks the plate after presentation and records correctness and RT
from both stimulus onset and choice onset. Chance accuracy is 25%.

## Controlled presentation

For data collection, leave the presentation and response defaults unchanged.
The presentation selector exposes three deliberately different scale policies:

| Mode | Sizing rule | Use |
| --- | --- | --- |
| **Calibrated fullscreen** | Renders a preregistered horizontal visual angle from measured screen width and viewing distance. | Formal data collection |
| **Expanded fullscreen** | Fits the largest possible plate while preserving the native 178:64 soundscape aspect ratio. | Training and uncalibrated pilot work |
| **Compact windowed** | Never enlarges beyond the native 712×256 visual raster and shrinks only when necessary. | UI debugging |

Expanded and compact modes may omit physical measurements; absent geometry
fields remain blank rather than receiving fabricated values. Their results are
labelled with distinct scale modes and should not be pooled with calibrated
trials as if presentation size were identical:

- **Fullscreen required:** fullscreen removes browser-window size as an
  uncontrolled variable. The plate always preserves its 178:64 aspect ratio;
  it is never cropped or stretched to fill an arbitrary rectangle.
- **Declared visual angle:** calibrated mode requires display width, viewing
  distance, and a target horizontal visual angle (50° by default). The browser
  computes the required physical and CSS width, refuses to present a trial if
  that size does not fit, and records target angle, achieved angle, and error.
  Expanded mode instead uses the largest native-aspect plate that fits;
  compact mode caps rendering at the native raster.
- **Response-matched start gate:** keyboard blocks begin with any ordinary key,
  so the participant never has to alternate between keyboard and trackpad.
  Pointer blocks retain the centre-crosshair click, which recentres the pointer
  before every attempt. Escape, Tab, modifier shortcuts, composing keys, and
  held-key repeats cannot start a keyboard trial.
- **Keyboard 1–4 response:** the recommended response mode avoids adding
  pointer-travel time to the discrimination RT. Pointer response remains
  available for pilot/debug blocks and is explicitly identified in the CSV.
- **Interruption handling:** hiding the page, leaving required fullscreen, or
  changing the viewport during an active attempt invalidates that attempt and
  restarts the same trial. Invalidated attempts and reasons are retained as
  audit metadata.
- **Timing and geometry audit:** every CSV row records planned and browser-observed
  stimulus/mask duration, viewport and stage dimensions, device pixel ratio,
  CSS pixels per audio column and row, scale mode, fullscreen state,
  browser-reported Web Audio latency, choice-onset RT, and
  stimulus-onset-to-response time. Stimulus, mask, and response onsets are
  synchronized to browser animation frames; response glyphs are preloaded.
- **Paired-order control:** in a mixed block, the all-visible and IR-substituted
  versions of each seeded plate are placed in opposite block halves. Which
  version comes first is balanced.

Keep display, audio volume, headphones, viewing distance, ambient illumination,
and response device fixed across compared blocks. Measure physical screen width
rather than copying a manufacturer diagonal: the experiment uses that value to
render the requested plate visual angle. Choose and preregister the target angle
before formal collection rather than changing it to improve performance.

The generator only approximates R/G/B/Y luminance matching in sRGB. Before a
formal visible-colour comparison, calibrate the display and establish
participant-specific equiluminance (for example with heterochromatic flicker
photometry). Likewise, fix and document sound-pressure level. Fullscreen and
browser timing controls remove software variability; they do not replace
physical display and audio calibration.

## Generate and run

From the repository root, first build the companion `IR-vOICe` repository so
its executable is available at `../IR-vOICe/raspivoice/Release/raspivoice`.
Then run:

```bash
python3 -m pip install -r requirements.txt
python3 generate_ishihara_stimuli.py --variants-per-glyph 1
python3 server.py 8001
```

Open <http://127.0.0.1:8001/ishihara/>. Omit the port to use port 8000. Increase
`--variants-per-glyph` for a larger stimulus bank. For asset/UI development,
add `--skip-audio`; runnable blocks require a bank generated with audio because
even non-probe trials contain the background carrier.

On this Mac, the packaged **IR Ishihara Simulator.app** performs the server and
browser steps automatically. The app contains its own copy of the UI, server,
and generated audio bank; completed CSVs are written to
`~/Library/Application Support/IR Ishihara Simulator/test_data/`.
Rebuild it after UI or stimulus changes with
`./tools/package_ishihara_app.sh` from the repository root.

If the executable lives elsewhere, use either:

```bash
python3 generate_ishihara_stimuli.py --raspivoice-bin /absolute/path/to/raspivoice
RASPIVOICE_BIN=/absolute/path/to/raspivoice python3 generate_ishihara_stimuli.py
```

## Implementation map

| Path | Responsibility |
| --- | --- |
| `generate_ishihara_stimuli.py` | Defines ambiguity families and channel recipes; renders matched plate/probe assets; invokes `raspivoice`; normalizes audio; writes the manifest. |
| `ishihara/index.html` | Setup, calibrated presentation controls, trial surface, response grid, and result screen. |
| `ishihara/app.js` | Trial state machine, Web Audio scheduling, masking, response logging, validation, interruption handling, and CSV export. |
| `ishihara/task_logic.mjs` | Seeded trial selection, paired mixed-condition scheduling, counterbalancing, and timing helpers. |
| `server.py` | Localhost-only static server plus safe `POST /api/save-run` CSV persistence. |
| `tests/` | Generator invariants, scheduling balance, timing helpers, and required UI/manifest contracts. |
| `ishihara_stimuli/` | Generated PNG/WAV/manifest bank; intentionally ignored by Git. |
| `test_data/` | Locally saved participant CSV files; intentionally ignored by Git. |

## Tests

Run from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/test_ishihara_schedule.mjs
```

Completed runs are downloaded by the browser and also saved by the local
server under `test_data/`. Generated stimuli and run data are ignored by Git.
