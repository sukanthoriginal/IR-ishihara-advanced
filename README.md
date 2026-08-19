# Advanced IR Ishihara

A standalone source-generalisation experiment for testing whether participants
can use spatial information delivered through an infrared vOICe channel to
disambiguate one-, two-, and three-glyph colour composites.

This repository is now advanced-only. It contains no L2 localisation task and
no legacy Ishihara application.

## What the experiment does

Each stimulus starts from a visible coloured-dot scaffold. A valid target is
formed only by adding diagnostic strokes:

- in **visual-only** mode, those strokes are visible and the 3.65-second
  presentation is silent;
- in **paired visible-versus-IR** mode, the same diagnostic geometry is either
  visible in the plate or carried by three left-to-right vOICe sweeps.

The visible comparator receives a background-only soundscape, while the IR
condition receives the spatial diagnostic plus the same background texture.
The two WAVs in every pair are RMS-matched, preventing overall loudness from
revealing the condition or answer. The plate remains static; only the audio
algorithm sweeps.

After 3 × 1.05-second sweeps and two 250 ms inter-sweep intervals (3.65 seconds
total), a 220 ms mask appears. The participant then chooses among four complete,
plausible interpretations. One is the target, one is the fully unchanged source
decoy, and two are close alternatives from the same source families.

Training sources provide trial feedback. Held-out test sources do not.

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
Visual-only sessions work without the audio binary. Mixed sessions require the
compiled `raspivoice` executable. Set its location when it is not in the usual
local checkout:

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

The launcher uses port 8137 and stores participant CSVs under
`~/Library/Application Support/Advanced IR Ishihara/test_data/`. Generated
session assets are stored separately in `session_cache/` and are never committed.

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
python3 -m unittest tests.test_advanced_generator tests.test_advanced_web_static
node tools/export_advanced_catalog.mjs --format=summary
```

## Scientific scope

The engine measures accuracy, unchanged-decoy capture, and response time for
visible and visible-plus-IR composites, including held-out source families. It
is suitable for pilot work and apparatus iteration. Its current foil ranking
and difficulty balance are explicit and tested, but should be frozen and
preregistered before confirmatory data collection.

A positive result can support behavioural use and generalisation of spatial
information carried by the IR audio channel. It does not by itself establish a
new colour quale, neural rewiring, or a specific neural mechanism.
