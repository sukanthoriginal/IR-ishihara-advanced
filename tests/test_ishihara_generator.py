import argparse
from collections import defaultdict
import json
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

import generate_ishihara_stimuli as generator


class IshiharaGeneratorTests(unittest.TestCase):
    def test_each_transformation_maps_a_complete_decoy_to_its_target(self):
        for level, tier in generator.COMPLEXITY_TIERS.items():
            with self.subTest(level=level):
                self.assertEqual(len(tier["glyphs"]), 4)
                for name in tier["glyphs"]:
                    transformation = generator.TRANSFORMATIONS[name]
                    scaffold, diagnostic, full = (
                        np.asarray(mask)
                        for mask in generator.make_variant_masks(name)
                    )
                    self.assertGreater(np.count_nonzero(scaffold), 0)
                    self.assertGreater(np.count_nonzero(diagnostic), 0)
                    self.assertFalse(np.any((scaffold > 0) & (diagnostic > 0)))
                    self.assertTrue(np.array_equal(
                        full > 0, (scaffold > 0) | (diagnostic > 0),
                    ))
                    self.assertTrue(np.array_equal(
                        scaffold,
                        np.asarray(generator.make_glyph_mask(
                            transformation["decoy_choice"],
                        )),
                    ))
                    self.assertTrue(np.array_equal(
                        full,
                        np.asarray(generator.make_glyph_mask(
                            transformation["target_choice"],
                        )),
                    ))
                    self.assertEqual(len(transformation["choices"]), 4)
                    self.assertIn(
                        transformation["target_choice"], transformation["choices"],
                    )
                    self.assertIn(
                        transformation["decoy_choice"], transformation["choices"],
                    )

    def test_scramble_preserves_histogram_but_changes_geometry(self):
        rng = np.random.default_rng(99)
        _, _, _, aligned, scrambled, _, background = generator.draw_trial_assets(
            "f-to-e-fork", generator.CHANNEL_RECIPES["r-ir"], rng,
        )
        aligned_values = np.asarray(aligned)
        scrambled_values = np.asarray(scrambled)
        self.assertFalse(np.array_equal(aligned_values, scrambled_values))
        self.assertTrue(
            np.array_equal(np.sort(aligned_values, axis=None), np.sort(scrambled_values, axis=None))
        )
        background_values = np.asarray(background)
        self.assertGreater(np.count_nonzero(background_values), 0)
        self.assertLess(int(background_values.max()), 40)
        bright = aligned_values >= 200
        self.assertGreater(np.count_nonzero(bright), 0)
        self.assertTrue(np.array_equal(aligned_values[~bright], background_values[~bright]))

    def test_scaffold_components_are_balanced_and_probe_is_disjoint(self):
        rng = np.random.default_rng(1729)
        visual, visible_only, neutral, _, _, components, _ = (
            generator.draw_trial_assets(
                "co-to-gq", generator.CHANNEL_RECIPES["rgb-ir"], rng,
            )
        )
        scaffold_counts = [sum(component) for component in components[:-1]]
        self.assertLessEqual(max(scaffold_counts) - min(scaffold_counts), 1)
        self.assertTrue(all(count > 0 for count in scaffold_counts))
        self.assertGreater(sum(components[-1]), 0)
        self.assertTrue(all(
            sum(component[index] for component in components) <= 1
            for index in range(len(components[0]))
        ))

        neutral_values = np.asarray(neutral)
        self.assertTrue(np.array_equal(neutral_values[..., 0], neutral_values[..., 1]))
        self.assertTrue(np.array_equal(neutral_values[..., 1], neutral_values[..., 2]))
        self.assertFalse(np.array_equal(np.asarray(visual), np.asarray(visible_only)))
        self.assertFalse(np.array_equal(np.asarray(visible_only), neutral_values))

    def test_retained_visible_channels_are_pixel_identical_in_paired_plates(self):
        for recipe_id, recipe in generator.CHANNEL_RECIPES.items():
            with self.subTest(recipe=recipe_id):
                visual, crossmodal, _, _, _, _, _ = generator.draw_trial_assets(
                    "co-to-gq", recipe, np.random.default_rng(2026),
                )
                visual_values = np.asarray(visual)
                crossmodal_values = np.asarray(crossmodal)
                retained_rgb = np.any(
                    crossmodal_values != crossmodal_values[..., :1], axis=2,
                )
                self.assertGreater(np.count_nonzero(retained_rgb), 0)
                self.assertTrue(np.array_equal(
                    visual_values[retained_rgb], crossmodal_values[retained_rgb],
                ))

    def test_decoy_plates_vary_but_probe_energy_is_matched_within_level(self):
        for level, tier in generator.COMPLEXITY_TIERS.items():
            for recipe_id, recipe in generator.CHANNEL_RECIPES.items():
                with self.subTest(level=level, recipe=recipe_id):
                    assets = [
                        generator.draw_trial_assets(
                            name, recipe, np.random.default_rng(1729 + level * 1009),
                        )
                        for name in tier["glyphs"]
                    ]
                    scaffold_plates = {np.asarray(item[1]).tobytes() for item in assets}
                    neutral_plates = {np.asarray(item[2]).tobytes() for item in assets}
                    visible_probe_plates = {np.asarray(item[0]).tobytes() for item in assets}
                    ir_maps = {np.asarray(item[3]).tobytes() for item in assets}
                    ir_histograms = {
                        np.sort(np.asarray(item[3]), axis=None).tobytes()
                        for item in assets
                    }
                    background_histograms = {
                        np.sort(np.asarray(item[6]), axis=None).tobytes()
                        for item in assets
                    }
                    self.assertGreaterEqual(len(scaffold_plates), 2)
                    self.assertEqual(len(neutral_plates), 1)
                    self.assertEqual(len(visible_probe_plates), 4)
                    self.assertEqual(len(ir_maps), 4)
                    self.assertEqual(len(ir_histograms), 1)
                    self.assertEqual(len(background_histograms), 1)

    def test_wav_rms_is_normalized_within_choice_family(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            params = wave._wave_params(2, 2, 48000, 100, "NONE", "not compressed")
            stimuli = []
            for index, amplitude in enumerate((300, 500, 700, 900)):
                item = {
                    "split": "train", "complexity_level": 1,
                    "channel_recipe_id": "r-ir", "seed": 1729,
                }
                for field in ("ir_wav", "ir_scrambled_wav", "ir_background_wav"):
                    path = out / f"{field}-{index}.wav"
                    generator._write_wav_samples(
                        path, params, np.full(200, amplitude, dtype=np.float64),
                    )
                    item[field] = path.name
                stimuli.append(item)

            generator.normalize_family_wav_rms(stimuli, out)
            for field in (
                "ir_wav_rms_int16", "ir_scrambled_wav_rms_int16",
                "ir_background_wav_rms_int16",
            ):
                values = [item[field] for item in stimuli]
                self.assertLess(max(values) - min(values), 0.02)

    def test_skip_audio_bank_has_paired_assets_and_split(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "bank"
            args = argparse.Namespace(
                seed=1729,
                variants_per_glyph=1,
                out=out,
                skip_audio=True,
                raspivoice_bin=generator.DEFAULT_RASPIVOICE_BIN,
            )
            manifest_path = generator.generate(args)
            manifest = json.loads(manifest_path.read_text())

            self.assertEqual(manifest["schema_version"], 8)
            self.assertEqual(manifest["task"], "ir-ishihara-ambiguous-metamers")
            self.assertFalse(manifest["audio_generated"])
            self.assertFalse(manifest["audio_rms_normalized_within_family"])
            self.assertEqual(
                len(manifest["stimuli"]),
                len(generator.ALL_GLYPHS) * 2 * len(generator.CHANNEL_RECIPES),
            )
            self.assertEqual(manifest["soundscape_sample_rate_hz"], 48000)
            self.assertEqual(manifest["soundscape_sample_count"], 50400)
            self.assertEqual(manifest["soundscape_samples_per_column"], 283)
            self.assertTrue(manifest["soundscape_uses_bspline"])
            self.assertEqual(manifest["plate_width"], 712)
            self.assertEqual(manifest["plate_height"], 256)
            self.assertEqual(manifest["soundscape_width"], 178)
            self.assertEqual(manifest["soundscape_height"], 64)
            self.assertEqual(manifest["visual_to_audio_scale_x"], 4)
            self.assertEqual(manifest["visual_to_audio_scale_y"], 4)
            self.assertEqual(
                manifest["coordinate_mapping"], "full-frame-normalized-no-crop",
            )
            self.assertEqual(
                {item["split"] for item in manifest["stimuli"]},
                {"train", "test"},
            )
            self.assertEqual(
                {item["complexity_level"] for item in manifest["stimuli"]},
                {1, 2, 3, 4},
            )
            self.assertEqual(
                {item["channel_recipe_id"] for item in manifest["stimuli"]},
                set(generator.CHANNEL_RECIPES),
            )
            self.assertEqual(len(manifest["metamer_families"]), 4)

            for item in manifest["stimuli"]:
                self.assertIsNone(item["ir_wav"])
                self.assertIsNone(item["ir_scrambled_wav"])
                self.assertIsNone(item["ir_background_wav"])
                self.assertEqual(item["component_count"], len(item["component_dot_counts"]))
                self.assertEqual(
                    item["scaffold_dot_count"], sum(item["component_dot_counts"][:-1]),
                )
                self.assertEqual(
                    item["diagnostic_dot_count"], item["component_dot_counts"][-1],
                )
                self.assertGreaterEqual(item["diagnostic_dot_count"], 3)
                self.assertEqual(len(item["response_choices"]), 4)
                self.assertIn(item["target_choice_id"], item["response_choices"])
                self.assertIn(item["decoy_choice_id"], item["response_choices"])
                self.assertNotEqual(item["target_choice_id"], item["decoy_choice_id"])
                self.assertIn(item["choice_structure"], {
                    "factorial-2x2", "completion-fork",
                    "multistroke-factorial", "one-of-three-position",
                })
                self.assertTrue(item["probe_state"])
                for key in (
                    "visual_composite_png", "visible_components_png", "neutral_plate_png",
                    "ir_input_png", "ir_scrambled_input_png", "ir_background_input_png",
                ):
                    self.assertTrue((out / item[key]).is_file())

            groups = defaultdict(list)
            for item in manifest["stimuli"]:
                key = (
                    item["split"], item["complexity_level"],
                    item["channel_recipe_id"], item["seed"],
                )
                groups[key].append(item)
            self.assertTrue(all(len(items) == 4 for items in groups.values()))
            for items in groups.values():
                scaffold_files = {
                    (out / item["visible_components_png"]).read_bytes() for item in items
                }
                neutral_files = {
                    (out / item["neutral_plate_png"]).read_bytes() for item in items
                }
                visual_files = {
                    (out / item["visual_composite_png"]).read_bytes() for item in items
                }
                ir_files = {
                    (out / item["ir_input_png"]).read_bytes() for item in items
                }
                diagnostic_counts = {
                    item["diagnostic_dot_count"] for item in items
                }
                ir_histograms = {
                    np.sort(np.asarray(generator.Image.open(
                        out / item["ir_input_png"],
                    )), axis=None).tobytes()
                    for item in items
                }
                self.assertGreaterEqual(len(scaffold_files), 2)
                self.assertEqual(len(neutral_files), 1)
                self.assertEqual(len(visual_files), 4)
                self.assertEqual(len(ir_files), 4)
                self.assertEqual(len(diagnostic_counts), 1)
                self.assertEqual(len(ir_histograms), 1)

    def test_completion_fork_reuses_one_decoy_for_distinct_targets(self):
        names = ("f-to-e-fork", "f-to-p-fork", "f-to-r-fork")
        triples = [generator.make_variant_masks(name) for name in names]
        self.assertEqual(len({item[0].tobytes() for item in triples}), 1)
        self.assertEqual(len({item[2].tobytes() for item in triples}), 3)
        self.assertEqual(
            {generator.TRANSFORMATIONS[name]["decoy_choice"] for name in names},
            {"glyph-f"},
        )

        assets = [
            generator.draw_trial_assets(
                name, generator.CHANNEL_RECIPES["g-ir"],
                np.random.default_rng(2738),
            )
            for name in names
        ]
        self.assertEqual(len({item[1].tobytes() for item in assets}), 1)
        self.assertEqual(len({item[0].tobytes() for item in assets}), 3)
        self.assertEqual(len({item[3].tobytes() for item in assets}), 3)

    def test_choice_grammars_block_one_global_completion_strategy(self):
        structures = {
            item["choice_structure"] for item in generator.TRANSFORMATIONS.values()
        }
        self.assertEqual(structures, {
            "factorial-2x2", "completion-fork",
            "multistroke-factorial", "one-of-three-position",
        })
        level_one_states = {
            generator.TRANSFORMATIONS[name]["probe_state"]
            for name in generator.COMPLEXITY_TIERS[1]["glyphs"]
        }
        self.assertEqual(level_one_states, {"left-only", "right-only", "both"})


if __name__ == "__main__":
    unittest.main()
