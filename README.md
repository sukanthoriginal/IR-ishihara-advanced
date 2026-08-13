# IR-vOICe simulator

Browser-based psychophysics tasks for measuring spatial and figure–ground
performance with vOICe soundscapes generated from simulated infrared frames.

## Tasks

- `web/`: the original point-localization task (3x3, 4x3 and 16x9).
- `ishihara/`: an IR colour-role substitution task. The same glyph mask is
  rendered either as a visible red/green boundary or as an IR-bright/IR-dim
  boundary delivered through the vOICe soundscape.

## Generate IR-Ishihara stimuli

The generator expects the companion `IR-vOICe` repository beside this one and
uses its real `raspivoice` binary:

```text
parent/
├── IR-vOICe/
└── IR-vOICe-simulator/
```

Build `IR-vOICe/raspivoice/Release/raspivoice`, then run:

```bash
python3 -m pip install -r requirements.txt
python3 generate_ishihara_stimuli.py
```

The companion repository can live elsewhere. Pass its executable explicitly:

```bash
python3 generate_ishihara_stimuli.py \
  --raspivoice-bin /Users/sukanth/Dev/Lossfunk/IR-vOICe/raspivoice/Release/raspivoice
```

Alternatively, set the `RASPIVOICE_BIN` environment variable.

For image/UI development without audio:

```bash
python3 generate_ishihara_stimuli.py --skip-audio
```

The generated bank is written to `ishihara_stimuli/` and intentionally
gitignored. By default it contains four training glyphs, four held-out glyphs,
three independently seeded dot layouts per glyph, aligned IR soundscapes and
spatially scrambled controls.

## Run

```bash
python3 server.py
```

Open:

- `http://127.0.0.1:8000/web/` for localization.
- `http://127.0.0.1:8000/ishihara/` for IR-Ishihara.

Completed blocks save to the gitignored `test_data/` directory. If the local
save endpoint is unavailable, the browser downloads the CSV instead.

## IR-Ishihara conditions

- **Visible:** green target dots among red background dots.
- **IR:** all dots share the same visible red distribution; only IR
  reflectance defines the target glyph.
- **IR scrambled:** preserves the count and intensity distribution of IR-bright
  dots while shuffling their positions.
- **Mixed:** balanced visible and aligned-IR trials in one block.

The plate is shown for the full 1.05-second soundscape duration, masked, and
followed by a four-image forced choice. Response time begins when the choices
appear, avoiding the left-to-right evidence-time confound in the serial audio
scan.
