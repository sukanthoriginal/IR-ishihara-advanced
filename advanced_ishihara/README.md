# Advanced IR-Ishihara grammar

This directory contains the design layer for the advanced compositional
experiment. It defines which stroke-addition transformations are possible and
how entire source families are assigned to training or held-out testing. It
does not yet claim that every raw combination has a valid dot plate, matched
four-choice foils, or generated vOICe audio.

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

## Next filtering stage

The raw catalog is intentionally broader than the final experiment. A later
filter must verify raster containment, exclude alias ambiguity, require at
least one changed mapping for main trials, construct equally plausible foils,
balance difficulty, and reserve identity-only sequences as explicit catch
controls. Audio should be generated and cached only for a frozen session
schedule, never for all 950,894 sequences.
