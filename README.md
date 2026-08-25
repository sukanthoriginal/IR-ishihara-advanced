# Advanced IR Ishihara

A standalone source-generalisation experiment for testing whether participants
can use spatial information delivered through an infrared vOICe channel to
disambiguate one-, two-, and three-glyph colour composites.

This repository is now advanced-only. It contains no L2 localisation task and
no legacy Ishihara application.

## What the experiment does

Each stimulus starts from a visible coloured-dot scaffold. A valid target is
formed only by adding diagnostic strokes. The default **Mixed visual vs IR**
design generates `N` distinct puzzles and presents each exactly once. It
structurally matches them and divides them between:

- **Visual diagnostic + neutral carrier** -- the complete diagnostic is
  visible and a background-only soundscape is played; and
- **IR-audio diagnostic** -- only the source scaffold is visible while the
  added strokes are carried by three left-to-right vOICe sweeps.

An even `N` gives exactly equal condition counts. For an odd `N`, the counts
differ by one and the reproducible run code deterministically assigns the
extra puzzle to Visual when its seed is even or IR when it is odd.

Two standalone designs are also available: **Visual baseline** shows the full
diagnostic in silence, while **IR only** shows the source scaffold with the IR
diagnostic audio. Under Advanced research mode, **Repeated pair** presents the
same puzzle once in each carrier-controlled condition, counterbalances which
condition appears first, and therefore produces `2N` presentations from `N`
stimuli.

Each background carrier is normalized to a low fixed RMS, and that exact
linear gain is also applied to its IR-probe counterfactual. The diagnostic's
energy and contrast are therefore preserved; the two complete WAVs are
intentionally not total-RMS matched. A peak ceiling prevents clipping. In
repeated pairs, the visual comparator receives the background-only carrier
while the IR condition receives the diagnostic plus that carrier. The plate
remains static; only the audio algorithm sweeps.

After 3 × 1.05-second sweeps and two 250 ms inter-sweep intervals (3.65 seconds
total), a 220 ms mask appears. The participant then chooses among four complete,
plausible interpretations. One is the target, one is the fully unchanged source
decoy, and two are close alternatives from the same source families.

Feedback is explicitly configurable and defaults to on. Enabling it for
held-out sources warns that doing so exposes test mappings; enabling it for
repeated pairs warns that the first presentation can reveal the answer before
its repeat.

## Configure a block

The setup screen exposes the controls needed for practice, simulation, and
experimental blocks:

- training (13/19 source families, exactly 2/3 of family mappings) or held-out
  testing (6/19 families, exactly 1/3 of mappings);
- default Mixed visual-versus-IR delivery, standalone silent Visual or IR-only
  delivery, and the advanced Repeated-pair design;
- 4--96 stimulus instances (30 by default), presented once in Mixed and standalone modes, with
  Repeated pair producing twice as many presentations;
- **Growing practice** from simpler to harder by default, or **Shuffled**
  progression across the balanced difficulty range;
- manual feedback (on by default), keyboard or pointer response, and compact,
  expanded, or physically calibrated presentation; and
- automatic balanced one-/two-/three-glyph composition, or a forced glyph
  count, under **Advanced block settings**.

The requested run code deterministically creates the first candidate. If that
candidate exceeds the participant's repeat ceiling, the history guard redraws
and records a different effective run code. Exact recreation therefore requires
the same settings and participant-history state, including the logged effective
code and redraw count. The automatic glyph policy divides base stimuli as
evenly as possible across one, two, and three glyphs; a seeded remainder rule
makes the preview and generated manifest agree for counts not divisible by
three.

Registered participant names appear in a local selection bar, and a new name
can be added without losing earlier participant-specific history. The selected
participant and chosen CSV results directory are remembered locally.
Before assets are rendered, the server draws the requested candidate normally
and compares its ordered transformation signatures with that participant's
exposure history. A candidate is accepted when no more than 10% of its base
stimulus slots are repeats; only candidates above that ceiling are redrawn.
The pre-session audit reports the exact eligible pool for the current split and
glyph setting, prior-history coverage, historical and within-candidate repeats,
the requested/effective run codes, and any redraw count. The deliberate second
presentation in Repeated-pair mode is not counted against this ceiling.

An exposure enters history when its plate is actually shown, even if the trial
is interrupted. Generating an unused block does not mark its puzzles as seen.
History starts with exposures recorded by this version; older CSV files are not
silently imported.

Immediately before Start, the server rechecks current history and atomically
reserves that participant for the block. This prevents two tabs from both
passing against the same stale history. The reservation is renewed at each
actual stimulus onset, released after exposure-history synchronization, and
expires after 60 minutes without activity if a tab is abandoned. The
reservation itself never records unshown candidate transformations as exposures.

Difficulty is an auditable structural estimate, not a claim about participant
performance. Its versioned score records glyph load, diagnostic subtlety, foil
similarity, and source-family ambiguity. The default Mixed signal design uses
this provisional estimate to match distinct puzzles across conditions,
prioritising glyph count, difficulty stratum, changed count, and then score.
This matching is for balance during piloting; it is not an empirically
validated measure of equal visual and IR difficulty.

The default **Growing practice** progression orders the estimate from lower to
higher; Repeated-pair growing blocks use two ordered passes. **Shuffled**
randomizes the selected difficulty range. Here “Mixed” names the signal design,
while “Shuffled” names trial order.

## Run locally

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Start the local server:

```bash
python3 server.py 8137
```

Then open [http://127.0.0.1:8137/advanced/](http://127.0.0.1:8137/advanced/).
Silent Visual sessions work without the audio binary. Mixed, IR-only, and
Repeated-pair sessions require the compiled `raspivoice` executable. Set its
location when it is not in the usual local checkout:

```bash
RASPIVOICE_BIN=/path/to/raspivoice python3 server.py 8137
```

Click **Generate and preload block** before testing. The engine freezes the
schedule, generates only that session's assets, validates every WAV, and waits
until the browser has decoded all images and audio. Nothing is generated during
a trial.

## Build the macOS launcher

```bash
ADVANCED_ISHIHARA_PYTHON=/path/to/python3 \
RASPIVOICE_BIN=/path/to/raspivoice \
tools/package_advanced_app.sh
```

The launcher uses port 8137. Its initial CSV directory and fixed participant
history database are under
`~/Library/Application Support/Advanced IR Ishihara/test_data/`; the CSV
directory can be changed and is remembered. Generated session assets are stored
separately in `session_cache/`. In a source checkout, the corresponding paths
are `test_data/` and `advanced_sessions/`. These local participant and generated
files are ignored by Git and are never committed.

The packaged launcher also mirrors every completed CSV, with the same filename
and bytes, into `/Users/sukanth/Dev/Lossfunk/ir-results/ishihara-advanced/`.
The original selected results directory remains the primary copy.

## Source-wise train/test split

All outcomes of a source stay in one assignment. A test source and its identity
mapping are never used during training.

| Assignment | Source families | Changed mappings | Identities | Total mappings |
| --- | ---: | ---: | ---: | ---: |
| Training | 13 | 47 | 13 | 60 |
| Held-out test | 6 | 24 | 6 | 30 |

The complete grammar contains 27 geometry classes, 19 transformable sources,
8 terminal targets, 71 addition-only transformations, and 27 identities. Its
raw ordered one- through three-position space contains 950,894 sequences;
930,455 contain at least one change. The session engine samples lazily rather
than generating that catalog in advance.

See [`advanced_ishihara/README.md`](advanced_ishihara/README.md) for the exact
source tables and grammar mathematics.

## Repository layout

- `advanced/` — participant UI and CSV logging
- `advanced_ishihara/` — canonical grammar and lazy session generator
- `shared/` — plate rasterisation, vOICe generation, timing, CSV, server, and
  launcher utilities
- `tests/` — grammar, generator, runtime, and static UI invariants
- `tools/` — catalog audit/export and macOS packaging

## Verify

```bash
npm test
python3 -m unittest tests.test_advanced_generator tests.test_advanced_web_static tests.test_local_participant_state
node tools/export_advanced_catalog.mjs --format=summary
```
