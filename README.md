# IR-vOICe simulator

Browser-based psychophysics tasks for measuring spatial and figure–ground
performance with vOICe soundscapes generated from simulated infrared frames.

## Tasks

- `web/`: the original point-localization task (3x3, 4x3 and 16x9).
- `ishihara/`: an ambiguity-grammar task in which a coherent visible scaffold
  becomes a different glyph only when a diagnostic visible-colour or IR-audio
  component is recovered. See [`ishihara/README.md`](ishihara/README.md) for
  the experimental rationale and controls.

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
gitignored. It spans four ambiguity families, four channel recipes, familiar
and held-out dot layouts, aligned and scrambled probes, and matched
background-only carriers. The carrier ensures that every condition has the
same three-sweep presentation timing and avoids sound-versus-silence cues.

## Run

```bash
python3 server.py
```

Open:

- `http://127.0.0.1:8000/web/` for localization.
- `http://127.0.0.1:8000/ishihara/` for IR-Ishihara.

Completed blocks save to the gitignored `test_data/` directory. If the local
save endpoint is unavailable, the browser downloads the CSV instead.

## IR ambiguity-grammar conditions

- **Mixed:** paired visible-probe and aligned-IR-probe trials for the same
  scaffold, geometry, and response set.
- **Visible composite:** the diagnostic probe is an ordinary visible colour;
  audio carries matched background texture only.
- **IR composite:** the visible scaffold omits the probe and aligned IR audio
  supplies it.
- **Visible-only, IR-only, and scrambled-IR controls:** measure decoy capture,
  probe decoding without the scaffold, and dependence on spatial geometry.

The RGB plate remains static while three 1.05-second vOICe soundscapes play.
It is then masked and followed by a four-image forced choice. The target,
coherent decoy, and two structurally matched alternatives are always present.
