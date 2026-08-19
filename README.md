# IR Ishihara Advanced

Advanced compositional discrimination experiments in which visible glyph
scaffolds are completed by spatial information carried through vOICe
soundscapes generated from simulated infrared frames.

The repository was seeded from `IR-vOICe-simulator` so the validated timing,
audio, plate, response, and CSV infrastructure can be reused. New advanced
work is isolated under [`advanced_ishihara/`](advanced_ishihara/README.md).
The inherited localization and original Ishihara applications remain present
temporarily while shared engine dependencies are identified; they are not part
of the advanced protocol.

## Tasks

- `advanced_ishihara/`: the source-to-target geometry grammar, source-wise
  train/test division, and lazy raw combination catalog.
- `web/`: the original point-localization task (3x3, 4x3 and 16x9).
- `ishihara/`: an ambiguity-grammar task in which a coherent visible scaffold
  becomes a different glyph only when a diagnostic visible-colour or IR-audio
  component is recovered. It includes a multimodal visible-versus-IR mode and
  a silent visual-only baseline with matched timing. See
  [`ishihara/README.md`](ishihara/README.md) for the experimental rationale and
  controls.

## Generate IR-Ishihara stimuli

The generator expects the companion `IR-vOICe` repository beside this one and
uses its real `raspivoice` binary:

```text
parent/
├── IR-vOICe/
└── IR-ishihara-advanced/
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

On macOS, double-click the packaged **IR Ishihara Simulator.app**. Its bundle
contains the server, task UI, and generated audio bank, so it does not need
permission to read the source repository. It starts a localhost server on port
8127, verifies the Ishihara page, and opens a standalone fullscreen Chrome
window. Completed CSVs are saved under
`~/Library/Application Support/IR Ishihara Simulator/test_data/`.

After generating the audio bank, build a fresh Desktop app with:

```bash
./tools/package_ishihara_app.sh
```

The packager refuses to overwrite an existing app. Move or rename the old app,
or pass a different absolute output path as its first argument.

The equivalent manual command is:

```bash
python3 server.py 8001
```

Open:

- `http://127.0.0.1:8001/web/` for localization.
- `http://127.0.0.1:8001/ishihara/` for IR-Ishihara.

When run manually, completed blocks save to the gitignored `test_data/`
directory. The packaged app uses its Application Support directory described
above. If the save endpoint is unavailable, the browser downloads the CSV.

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
