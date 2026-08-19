# Advanced IR-Ishihara grammar

This directory contains the canonical grammar and lazy session builder for the
advanced compositional experiment. It defines which stroke-addition
transformations are possible, assigns complete source families to training or
held-out testing, rasterises selected combinations, constructs four-choice
interpretations, and generates a frozen session's vOICe audio on demand.

## Geometry model

The grammar contains 27 distinguishable geometry classes. `0/O` and `8/B`
each remain one class until their aliases receive distinct masks. Nineteen
geometries can gain strokes and reach another supported geometry; eight are
terminal targets.

The 32 minimal addition-only edges form a directed acyclic graph. Its
transitive closure contains 71 valid changed mappings. Adding all 27 geometric
identities produces 98 raw mappings.

Terminal identities remain in the raw catalog as possible unchanged context.
They are tagged separately because, unlike an identity such as `L→L`, their
source has no supported changed outcome.

## Source-wise train/test division

A source and its entire family stay together. For example, no `J→…` outcome,
including `J→J`, appears in training because `J` is a held-out test source.
Targets are never divided within a source.

| Bucket | Sources | Changed mappings | Source identities | Family mappings |
| --- | --- | ---: | ---: | ---: |
| Train | `1, L, T, ∧, 3, 7, 9, 0/O, C, F, P, U, 6` | 47 | 13 | 60 |
| Test | `Γ, V, J, 4, E, H` | 24 | 6 | 30 |

The 13:6 source count is the closest integer split of 19 sources to 2:1. The
chosen families happen to yield an exact 60:30 mapping ratio after each
source's identity is included. The eight terminal geometries are target-only
for the source split.

### Training families

| Source | Changed targets | Family size including identity |
| --- | --- | ---: |
| `1` | `3, 4, 7, 8/B, 9, 0/O, H, J, Q, U` | 11 |
| `L` | `8/B, 0/O, C, E, G, Q, U, 6` | 9 |
| `T` | `I` | 2 |
| `∧` | `A` | 2 |
| `3` | `8/B, 9` | 3 |
| `7` | `3, 8/B, 9, 0/O, Q` | 6 |
| `9` | `8/B` | 2 |
| `0/O` | `8/B, Q` | 3 |
| `C` | `8/B, 0/O, E, G, Q, 6` | 7 |
| `F` | `8/B, E, P, R, 6` | 6 |
| `P` | `8/B, R` | 3 |
| `U` | `8/B, 0/O, Q` | 4 |
| `6` | `8/B` | 2 |

### Held-out test families

| Source | Changed targets | Family size including identity |
| --- | --- | ---: |
| `Γ` | `8/B, 0/O, C, E, F, G, P, Q, R, 6` | 11 |
| `V` | `X, Y` | 3 |
| `J` | `3, 8/B, 9, 0/O, Q, U` | 7 |
| `4` | `8/B, 9, H` | 4 |
| `E` | `8/B, 6` | 3 |
| `H` | `8/B` | 2 |

## Raw ordered catalog

Each position chooses one of 98 raw mappings. Position is meaningful and
repetition is allowed, so the raw one- through three-position space is:

| Positions | Raw sequences |
| ---: | ---: |
| 1 | 98 |
| 2 | 9,604 |
| 3 | 941,192 |
| **Total** | **950,894** |

Of these, 20,439 are all-identity sequences and 930,455 contain at least one
change. Nothing is generated eagerly: a sequence is reconstructed from its
length and integer rank, giving it a stable ID such as `raw-v1-l3-r42`.

Inspect the counts without generating the catalog:

```bash
node tools/export_advanced_catalog.mjs --format=summary
```

The CSV and JSONL formats deliberately stream the entire raw catalog and can
therefore produce large files:

```bash
node tools/export_advanced_catalog.mjs --format=csv
node tools/export_advanced_catalog.mjs --format=jsonl
```

## Implemented session filter

The raw catalog remains intentionally broader than any one block. The current
session engine:

- validates that the drawn segment containment exactly matches all 71 grammar
  transformations;
- samples ordered one-, two-, and three-position trials from only the selected
  source assignment;
- requires at least one change on every main trial while allowing unchanged
  positions as within-composite context;
- supplies the target, the complete unchanged-source decoy, and the two closest
  same-family alternatives;
- allocates automatic one-, two-, and three-glyph quotas as evenly as possible,
  with reproducible remainder assignment;
- plans candidate signatures before rendering, reports the exact eligible pool,
  and redraws only participant-specific candidates whose repeat slots exceed
  the 10% pre-session ceiling;
- records a versioned estimated structural-difficulty score and its raw
  components for every stimulus;
- generates and validates only the frozen session's assets;
- supports silent visual, standalone IR-audio, distinct-stimulus mixed, and
  repeated-stimulus paired comparison blocks;
- assigns mixed stimuli to carrier-controlled visual-background and IR-audio
  conditions without repeating a puzzle, provisionally matching within glyph
  count and then by structural-difficulty features;
- logs every mixed match, its structural-score gap, and condition balance by
  glyph count and difficulty stratum (this structural matching is provisional,
  not an empirically validated equivalence claim);
- normalises each background carrier to a low fixed RMS and applies the same
  gain to its probe counterfactual, preserving diagnostic contrast rather than
  equalising the complete WAVs' total RMS, with a declared peak ceiling; and
- counterbalances paired order while logging presentation order, pass, lag,
  and displayed choice order.

The final confirmatory protocol should freeze the foil-ranking rule, establish
difficulty strata from pilot data, and decide whether identity-only sequences
are included as explicit catch trials. The raw grammar and stable IDs make those
filters auditable without ever pre-generating all 950,894 possibilities.
