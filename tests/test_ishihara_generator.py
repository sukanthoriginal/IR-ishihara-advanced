import argparse
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import generate_ishihara_stimuli as generator


class IshiharaGeneratorTests(unittest.TestCase):
    def test_glyph_masks_are_nonempty_and_distinct(self):
        masks = [np.asarray(generator.make_glyph_mask(name)) for name in generator.ALL_GLYPHS]
        self.assertTrue(all(np.count_nonzero(mask) > 0 for mask in masks))
        fingerprints = {mask.tobytes() for mask in masks}
        self.assertEqual(len(fingerprints), len(generator.ALL_GLYPHS))

    def test_scramble_preserves_histogram_but_changes_geometry(self):
        rng = np.random.default_rng(99)
        _, _, aligned, scrambled = generator.draw_trial_assets("star", rng)
        aligned_values = np.asarray(aligned)
        scrambled_values = np.asarray(scrambled)
        self.assertFalse(np.array_equal(aligned_values, scrambled_values))
        self.assertTrue(
            np.array_equal(np.sort(aligned_values, axis=None), np.sort(scrambled_values, axis=None))
        )

    def test_skip_audio_bank_has_paired_assets_and_split(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "bank"
            args = argparse.Namespace(
                seed=1729,
                variants_per_glyph=1,
                out=out,
                skip_audio=True,
            )
            manifest_path = generator.generate(args)
            manifest = json.loads(manifest_path.read_text())

            self.assertEqual(manifest["task"], "ir-ishihara-role-substitution")
            self.assertFalse(manifest["audio_generated"])
            self.assertEqual(len(manifest["stimuli"]), len(generator.ALL_GLYPHS))
            self.assertEqual(
                {item["split"] for item in manifest["stimuli"]},
                {"train", "test"},
            )

            for item in manifest["stimuli"]:
                self.assertIsNone(item["ir_wav"])
                self.assertIsNone(item["ir_scrambled_wav"])
                for key in (
                    "visible_png", "ir_hidden_png", "ir_input_png",
                    "ir_scrambled_input_png",
                ):
                    self.assertTrue((out / item[key]).is_file())


if __name__ == "__main__":
    unittest.main()
