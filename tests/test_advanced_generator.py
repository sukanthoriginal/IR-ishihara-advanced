import json
import random
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np
from PIL import Image

from advanced_ishihara import generate_session as generator
from shared.plate import (
    AUDIO_HEIGHT,
    AUDIO_WIDTH,
    GEOMETRY_SEGMENTS,
    PLATE_HEIGHT,
    PLATE_WIDTH,
    render_trial_images,
    segment_closure_relations,
)
from shared.soundscape import normalize_wav_rms, wav_rms_int16


class AdvancedGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grammar = generator.load_grammar()
        cls.family_by_source = {
            family["sourceId"]: family
            for family in cls.grammar["sourceFamilies"]
        }

    def test_drawn_geometries_exactly_implement_the_canonical_closure(self):
        expected = {
            (mapping["sourceId"], mapping["targetId"])
            for mapping in self.grammar["mappings"]
            if mapping["changed"]
        }
        self.assertEqual(set(GEOMETRY_SEGMENTS), {
            geometry["id"] for geometry in self.grammar["geometries"]
        })
        self.assertEqual(segment_closure_relations(), expected)
        self.assertEqual(len(expected), 71)

    def test_visual_session_is_source_split_complete_and_cached(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path, manifest = generator.prepare_session(
                {"split": "test", "mode": "visual-only", "trialCount": 9, "seed": 8127},
                root,
            )
            self.assertEqual(manifest["stimulus_duration_ms"], 3650)
            self.assertEqual(manifest["coordinate_mapping"], "full-frame-normalized-no-crop")
            self.assertFalse(manifest["audio_generated"])
            self.assertEqual(len(manifest["trials"]), 9)
            self.assertEqual(len(manifest["stimuli"]), 9)
            self.assertEqual({len(item["source_ids"]) for item in manifest["stimuli"]}, {1, 2, 3})

            for item in manifest["stimuli"]:
                self.assertTrue(all(
                    self.family_by_source[source]["split"] == "test"
                    for source in item["source_ids"]
                ))
                self.assertGreaterEqual(item["changed_count"], 1)
                self.assertEqual(len(item["response_choices"]), 4)
                interpretations = {
                    tuple(choice["target_ids"])
                    for choice in item["response_choices"]
                }
                self.assertEqual(len(interpretations), 4)
                self.assertIn(tuple(item["target_ids"]), interpretations)
                self.assertIn(tuple(item["source_ids"]), interpretations)
                self.assertNotEqual(item["target_choice_id"], item["decoy_choice_id"])
                for mapping_id, source, target in zip(
                    item["mapping_ids"], item["source_ids"], item["target_ids"]
                ):
                    self.assertEqual(mapping_id, f"{source}--{target}")
                    outcomes = [
                        source,
                        *self.family_by_source[source]["changedTargetIds"],
                    ]
                    self.assertIn(target, outcomes)
                for key in ("ir_plate_png", "visual_plate_png", "ir_input_png", "background_input_png"):
                    self.assertTrue((path.parent / item[key]).is_file())

            cached_path, cached_manifest = generator.prepare_session(
                {"split": "test", "mode": "visual-only", "trialCount": 9, "seed": 8127},
                root,
            )
            self.assertEqual(cached_path, path)
            self.assertEqual(cached_manifest, manifest)

    def test_visual_and_ir_assets_share_geometry_but_not_probe_visibility(self):
        family = self.family_by_source["l"]
        choices = [["c"], ["l"], ["u"], ["zero-o"]]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets = render_trial_images(
                ["l"], ["c"], choices, root, "probe", seed=19,
            )
            ir_plate = np.asarray(Image.open(root / assets["ir_plate_png"]))
            visual_plate = np.asarray(Image.open(root / assets["visual_plate_png"]))
            probe = np.asarray(Image.open(root / assets["ir_input_png"]))
            background = np.asarray(Image.open(root / assets["background_input_png"]))
            self.assertEqual(ir_plate.shape, (PLATE_HEIGHT, PLATE_WIDTH, 3))
            self.assertEqual(visual_plate.shape, (PLATE_HEIGHT, PLATE_WIDTH, 3))
            self.assertEqual(probe.shape, (AUDIO_HEIGHT, AUDIO_WIDTH))
            self.assertFalse(np.array_equal(ir_plate, visual_plate))
            changed = np.any(ir_plate != visual_plate, axis=2)
            self.assertGreater(np.count_nonzero(changed), 0)
            self.assertTrue(np.all(
                np.ptp(ir_plate[changed].astype(np.int16), axis=1) < 10
            ))
            self.assertTrue(np.all(
                visual_plate[changed, 0].astype(np.int16)
                - visual_plate[changed, 2].astype(np.int16) > 100
            ))
            self.assertTrue(np.all(
                visual_plate[changed, 1].astype(np.int16)
                - visual_plate[changed, 2].astype(np.int16) > 80
            ))
            self.assertGreater(np.count_nonzero(probe != background), 0)
            self.assertTrue(np.all(probe[probe != background] >= 210))
            self.assertIn("c", family["changedTargetIds"])

    def test_mixed_schedule_pairs_every_stimulus_once_per_condition(self):
        stimuli = []
        for index in range(4):
            stimuli.append({
                "stimulus_id": f"s-{index}",
                "ir_plate_png": f"ir-{index}.png",
                "visual_plate_png": f"visual-{index}.png",
                "ir_probe_wav": f"probe-{index}.wav",
                "background_wav": f"background-{index}.wav",
            })
        schedule = generator.make_schedule(
            stimuli,
            {"mode": "mixed"},
            random.Random(8),
        )
        self.assertEqual(len(schedule), 8)
        self.assertTrue(all(item["pair_position"] == 1 for item in schedule[:4]))
        self.assertTrue(all(item["pair_position"] == 2 for item in schedule[4:]))
        by_stimulus = {}
        for trial in schedule:
            by_stimulus.setdefault(trial["stimulus_id"], []).append(trial)
        for trials in by_stimulus.values():
            self.assertEqual({item["condition"] for item in trials}, {
                "visible-composite", "ir-composite",
            })
            visible = next(item for item in trials if item["condition"] == "visible-composite")
            ir_trial = next(item for item in trials if item["condition"] == "ir-composite")
            self.assertEqual(visible["audio_content"], "background-only-carrier")
            self.assertEqual(ir_trial["audio_content"], "diagnostic-ir-probe-plus-background-carrier")

    def test_wav_pair_rms_normalization_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = []
            for index, amplitude in enumerate((200, 1_800)):
                path = root / f"tone-{index}.wav"
                samples = np.full(50_400 * 2, amplitude, dtype="<i2")
                with wave.open(str(path), "wb") as wav_file:
                    wav_file.setnchannels(2)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(48_000)
                    wav_file.writeframes(samples.tobytes())
                paths.append(path)
            measured = normalize_wav_rms(paths, target_rms=1_000)
            self.assertEqual(set(measured), {path.name for path in paths})
            for path in paths:
                self.assertLess(abs(wav_rms_int16(path) - 1_000), 0.5)
                with wave.open(str(path), "rb") as wav_file:
                    self.assertEqual(wav_file.getparams()[:4], (2, 2, 48_000, 50_400))


if __name__ == "__main__":
    unittest.main()
