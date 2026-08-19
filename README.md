# IR Ishihara Advanced

An advanced compositional-discrimination experiment in which a visible glyph
scaffold can remain unchanged or become another plausible glyph when spatial
information is supplied through an infrared vOICe soundscape.

## Project status

The geometry grammar and source-wise train/test division are implemented and
audited. The advanced plate generator, matched response foils, participant UI,
audio cache, launcher, and final trial filters are not implemented yet.

The repository was seeded from
[`IR-vOICe-simulator`](https://github.com/sukanthoriginal/IR-vOICe-simulator)
to preserve its validated soundscape timing and experiment infrastructure.
Inherited applications remain temporarily as migration material; they are not
the Advanced Ishihara protocol and should not be used to collect advanced-study
data.

## Current experimental model

The committed raw grammar defines:

- 27 distinguishable geometry classes
- 19 transformable source geometries
- 8 terminal target geometries
- 71 valid addition-only changed mappings
- 27 unchanged identity mappings
- 98 raw atomic mappings

Training and testing are divided by complete source family. A source's identity
and every reachable target always follow the same assignment; targets from one
source are never split between training and held-out testing.

| Assignment | Source families | Changed mappings | Identities | Total mappings |
| --- | ---: | ---: | ---: | ---: |
| Training | 13 | 47 | 13 | 60 |
| Held-out test | 6 | 24 | 6 | 30 |

The eight terminal identities remain tagged in the raw catalog as possible
unchanged context but are not transformable source families.

See [`advanced_ishihara/README.md`](advanced_ishihara/README.md) for the exact
geometry graph and complete train/test tables.

## Raw ordered possibility space

A trial may contain one, two, or three ordered mapping positions. Position is
meaningful and repetition is allowed.

| Positions | Raw sequences |
| ---: | ---: |
| 1 | 98 |
| 2 | 9,604 |
| 3 | 941,192 |
| **Total** | **950,894** |

Of these sequences, 930,455 contain at least one change. The catalog is
addressed lazily by stable IDs; importing it does not generate hundreds of
thousands of plates or audio files.

These are mathematical possibilities, not automatically valid experimental
trials. The next filtering stage must validate geometry, construct equally
plausible four-choice interpretations, balance difficulty, and separate main
trials from explicit no-change catches.

## Repository layout

- `advanced_ishihara/grammar.mjs` — canonical geometry graph, mappings,
  source-family split, stable IDs, and lazy enumeration
- `advanced_ishihara/README.md` — detailed protocol and source tables
- `tests/test_advanced_grammar.mjs` — count and split invariants
- `tools/export_advanced_catalog.mjs` — streaming audit/export utility
- `ishihara/` and `generate_ishihara_stimuli.py` — temporary reference
  implementation from the original Ishihara task

The inherited L2 localization files are scheduled for removal. Reusable audio,
timing, plate, response, CSV, server, and launcher functionality will be
extracted into a shared engine before the original Ishihara application is
removed.

## Audit the grammar

Run the invariant tests:

```bash
node tests/test_advanced_grammar.mjs
```

Inspect counts without materializing the catalog:

```bash
node tools/export_advanced_catalog.mjs --format=summary
```

CSV and JSONL export stream the complete 950,894-entry raw catalog and should
only be used when a full external audit is actually required.

## Implementation sequence

1. Remove inherited L2 localization code and assets.
2. Extract reusable experiment infrastructure into `shared/`.
3. Implement and validate the advanced geometry rasterizer.
4. Build ambiguity-preserving foils and difficulty filters.
5. Freeze a session schedule before generating and caching its audio.
6. Build the advanced participant UI, logging schema, launcher, and controls.
7. Remove the inherited original Ishihara application after feature parity.

## Scientific scope

The intended measurements are accuracy, decoy capture, false alarms, and
response time for visible-only and visible-plus-IR composites. A positive
result could demonstrate behavioural use and generalisation of spatial
information carried through the IR audio channel. It would not by itself
establish a new colour quale, neural rewiring, or a specific neural mechanism.
