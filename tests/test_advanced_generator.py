import math
import random
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from advanced_ishihara import generate_session as generator
from shared import plate as plate_renderer
from shared import soundscape
from shared.plate import (
    ALIGNED_DISPLACEMENT_AUDIO_PIXELS,
    ALIGNED_VISUAL_CARRIER_VERSION,
    ALIGNED_VISUAL_CARRIER_DOT_COUNT,
    ALIGNED_VISUAL_CARRIER_OCCUPIED_PIXEL_COUNT,
    ALIGNED_VISUAL_CARRIER_RADIUS_HISTOGRAM,
    ALIGNED_VISUAL_COPY_COLOUR,
    ALIGNED_VISUAL_DENSITY_EQUIVALENCE_VERSION,
    ALIGNED_VISUAL_DOT_STEP,
    ALIGNED_VISUAL_PALETTE_VERSION,
    ALIGNED_VISUAL_PAIR_AXIS,
    ALIGNED_VISUAL_PAIR_OFFSET_PIXELS,
    ALIGNED_VISUAL_SUBDOT_RADII,
    AUDIO_HEIGHT,
    AUDIO_WIDTH,
    GEOMETRY_SEGMENTS,
    PLATE_HEIGHT,
    PLATE_WIDTH,
    SOURCE_COLOURS,
    render_trial_images,
    segment_closure_relations,
)
from shared.soundscape import (
    AUDIO_NORMALIZATION_METHOD,
    CARRIER_TARGET_RMS_INT16,
    EXPECTED_WAV_FRAMES,
    PEAK_CEILING_DBFS,
    apply_carrier_referenced_gain,
    wav_peak_int16,
    wav_rms_int16,
)


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

    def test_settings_normalize_new_contract_and_legacy_requests(self):
        self.assertEqual(generator.normalize_settings({}), {
            "split": "train",
            "signalMode": "mixed",
            "baseStimulusCount": 30,
            "glyphComposition": "automatic",
            "progression": "growing",
            "feedbackEnabled": True,
            "seed": 1729,
            "schemaVersion": generator.SCHEMA_VERSION,
        })
        normalized = generator.normalize_settings({
            "split": "test",
            "signalMode": "ir",
            "uniqueStimulusCount": 17,
            "glyphComposition": 3,
            "progression": "mixed",
            "feedbackEnabled": False,
            "seed": 0xFFFFFFFF,
        })
        self.assertEqual(normalized["baseStimulusCount"], 17)
        self.assertEqual(normalized["glyphComposition"], "3")
        self.assertEqual(normalized["signalMode"], "ir")
        self.assertEqual(
            generator.normalize_settings({"progression": "glyph-growing"})[
                "progression"
            ],
            "glyph-growing",
        )
        self.assertEqual(
            generator.normalize_settings({"signalMode": "mixed"})["signalMode"],
            "mixed",
        )
        legacy = generator.normalize_settings({
            "mode": "mixed", "trialCount": 12, "seed": 3,
        })
        self.assertEqual(legacy["signalMode"], "paired")
        self.assertEqual(legacy["baseStimulusCount"], 6)
        aligned = generator.normalize_settings({
            "signalMode": "mixed-aligned",
            "mixedConditionRatio": "2:2:2:4",
        })
        self.assertEqual(aligned["mixedConditionRatio"], "1:1:1:2")
        self.assertEqual(aligned["mixedConditionWeights"], [1, 1, 1, 2])

        invalid_settings = (
            {"signalMode": "sound"},
            {"baseStimulusCount": 3},
            {"baseStimulusCount": 97},
            {"glyphComposition": "4"},
            {"progression": "random-ish"},
            {"feedbackEnabled": "false"},
            {"seed": -1},
            {"seed": 0x1_0000_0000},
            {"signalMode": "mixed-aligned", "mixedConditionRatio": "1:1:2"},
            {"signalMode": "mixed-aligned", "mixedConditionRatio": "1:0:1:2"},
        )
        for settings in invalid_settings:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                generator.normalize_settings(settings)

    def test_cli_defaults_to_distinct_stimulus_mixed_comparison(self):
        with patch.object(generator.sys, "argv", ["generate_session.py"]):
            arguments = generator.parse_args()
        self.assertEqual(arguments.signal_mode, "mixed")
        self.assertEqual(arguments.stimuli, 30)
        self.assertEqual(arguments.progression, "growing")
        self.assertTrue(arguments.feedback)

    def test_automatic_glyph_quotas_are_even_with_seeded_remainders(self):
        self.assertEqual(
            generator.glyph_count_quotas(12, "automatic", 99),
            {1: 4, 2: 4, 3: 4},
        )
        self.assertEqual(
            generator.glyph_count_quotas(10, "automatic", 0),
            {1: 4, 2: 3, 3: 3},
        )
        self.assertEqual(
            generator.glyph_count_quotas(11, "automatic", 1),
            {1: 3, 2: 4, 3: 4},
        )
        for count in range(4, 97):
            quotas = generator.glyph_count_quotas(count, "automatic", 2718)
            self.assertEqual(sum(quotas.values()), count)
            self.assertLessEqual(max(quotas.values()) - min(quotas.values()), 1)
        for glyph_count in (1, 2, 3):
            expected = {1: 0, 2: 0, 3: 0}
            expected[glyph_count] = 23
            self.assertEqual(
                generator.glyph_count_quotas(23, str(glyph_count), 4),
                expected,
            )

    def test_aligned_condition_quotas_default_to_one_one_one_two(self):
        self.assertEqual(
            generator.weighted_condition_quotas(30, (1, 1, 1, 2), 1729),
            (6, 6, 6, 12),
        )
        self.assertEqual(sum(
            generator.weighted_condition_quotas(31, (1, 1, 1, 2), 9)
        ), 31)
        matrix = generator.condition_glyph_quota_matrix(
            (6, 6, 6, 12), {1: 10, 2: 10, 3: 10}, 1729,
        )
        self.assertEqual(matrix, {
            "visual_background_audio": {1: 2, 2: 2, 3: 2},
            "visual_aligned_overlay": {1: 2, 2: 2, 3: 2},
            "visual_aligned_ir_audio": {1: 2, 2: 2, 3: 2},
            "ir_audio": {1: 4, 2: 4, 3: 4},
        })
        for condition, quota in zip(
            generator.ALIGNED_MIXED_CONDITIONS, (6, 6, 6, 12),
        ):
            self.assertEqual(sum(matrix[condition].values()), quota)
        for glyph in (1, 2, 3):
            self.assertEqual(sum(row[glyph] for row in matrix.values()), 10)
        for count in range(4, 97):
            glyph_quotas = generator.glyph_count_quotas(
                count, "automatic", count * 17,
            )
            for weights in ((1, 1, 1, 2), (2, 3, 5, 7), (1, 1, 1, 37)):
                condition_quotas = generator.weighted_condition_quotas(
                    count, weights, count * 17,
                )
                apportioned = generator.condition_glyph_quota_matrix(
                    condition_quotas, glyph_quotas, count * 17,
                )
                self.assertEqual(
                    [sum(apportioned[condition].values())
                     for condition in generator.ALIGNED_MIXED_CONDITIONS],
                    list(condition_quotas),
                )
                self.assertEqual(
                    {glyph: sum(row[glyph] for row in apportioned.values())
                     for glyph in (1, 2, 3)},
                    glyph_quotas,
                )

    def test_lightweight_plan_reports_exact_runnable_catalog_counts(self):
        self.assertEqual(
            generator.eligible_transformation_counts(self.grammar, "train"),
            {1: 37, 2: 3431, 3: 213803},
        )
        self.assertEqual(
            generator.eligible_transformation_counts(self.grammar, "test"),
            {1: 19, 2: 864, 3: 26784},
        )
        self.assertEqual(
            generator.mixed_aligned_eligible_counts(self.grammar, "train"),
            {1: 60, 2: 3600, 3: 216000},
        )
        self.assertEqual(
            generator.mixed_aligned_eligible_counts(self.grammar, "test"),
            {1: 30, 2: 900, 3: 27000},
        )
        plan = generator.plan_session({
            "split": "test",
            "signalMode": "visual",
            "baseStimulusCount": 12,
            "glyphComposition": "automatic",
            "seed": 8127,
        })
        self.assertEqual(plan["eligible_transformation_count"], 27667)
        self.assertEqual(len(plan["base_specs"]), 12)
        self.assertEqual(sum(plan["glyph_count_quotas"].values()), 12)
        self.assertEqual(
            len({item["transformationSignature"] for item in plan["base_specs"]}),
            12,
        )

    def test_forced_one_glyph_high_count_cycles_before_balanced_reuse(self):
        families = [
            family for family in self.grammar["sourceFamilies"]
            if family["split"] == "test"
        ]
        available_signature_count = sum(
            len(family["changedTargetIds"])
            for family in families
            if family["familySize"] >= 4
        )
        specs = generator.select_base_trials(
            families,
            96,
            random.Random(901),
            glyph_lengths=[1] * 96,
        )
        self.assertEqual(len(specs), 96)
        self.assertTrue(all(len(item["sourceIds"]) == 1 for item in specs))
        first_cycle = specs[:available_signature_count]
        self.assertEqual(
            len({item["transformationSignature"] for item in first_cycle}),
            available_signature_count,
        )
        repetitions_by_signature = {}
        for item in specs:
            repetitions_by_signature.setdefault(
                item["transformationSignature"], [],
            ).append(item["mappingRepetitionIndex"])
        self.assertLessEqual(
            max(map(len, repetitions_by_signature.values()))
            - min(map(len, repetitions_by_signature.values())),
            1,
        )
        self.assertTrue(all(
            indexes == list(range(1, len(indexes) + 1))
            for indexes in repetitions_by_signature.values()
        ))

    def test_visual_session_has_difficulty_metadata_schedule_and_cache(self):
        settings = {
            "split": "test",
            "signalMode": "visual",
            "baseStimulusCount": 9,
            "glyphComposition": "automatic",
            "progression": "growing",
            "feedbackEnabled": False,
            "seed": 8127,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path, manifest = generator.prepare_session(settings, root)
            self.assertEqual(manifest["schema_version"], generator.SCHEMA_VERSION)
            self.assertEqual(manifest["render_version"], generator.RENDER_VERSION)
            self.assertEqual(
                manifest["audio_render_version"], generator.AUDIO_RENDER_VERSION,
            )
            self.assertEqual(manifest["stimulus_duration_ms"], 3650)
            self.assertEqual(manifest["coordinate_mapping"], "full-frame-normalized-no-crop")
            self.assertFalse(manifest["audio_generated"])
            self.assertFalse(manifest["feedback_enabled"])
            self.assertEqual(manifest["base_stimulus_count"], 9)
            self.assertEqual(manifest["total_presentation_count"], 9)
            self.assertEqual(manifest["glyph_count_distribution"], {"1": 3, "2": 3, "3": 3})
            self.assertEqual(manifest["condition_distribution"], {"visual_silent": 9})
            self.assertEqual(manifest["difficulty_model_version"], "estimated-v1")
            self.assertEqual(len(manifest["trials"]), 9)
            self.assertEqual(len(manifest["stimuli"]), 9)

            scores = [item["estimated_difficulty_score"] for item in manifest["trials"]]
            self.assertEqual(scores, sorted(scores))
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
                self.assertTrue(0 <= item["estimated_difficulty_score"] <= 100)
                self.assertEqual(
                    set(item["difficulty_components"]),
                    set(generator.DIFFICULTY_COMPONENT_NAMES),
                )
                self.assertTrue(all(
                    0 <= value <= 1
                    for value in item["difficulty_components"].values()
                ))
                self.assertIn(item["difficulty_stratum"], {"easy", "moderate", "hard"})
                self.assertGreaterEqual(item["mapping_repetition_index"], 1)
                for mapping_id, source, target in zip(
                    item["mapping_ids"], item["source_ids"], item["target_ids"]
                ):
                    self.assertEqual(mapping_id, f"{source}--{target}")
                    self.assertIn(target, [
                        source,
                        *self.family_by_source[source]["changedTargetIds"],
                    ])
                for key in (
                    "ir_plate_png", "visual_plate_png", "ir_input_png",
                    "background_input_png",
                ):
                    self.assertTrue((path.parent / item[key]).is_file())

            for trial in manifest["trials"]:
                stimulus = next(
                    item for item in manifest["stimuli"]
                    if item["stimulus_id"] == trial["stimulus_id"]
                )
                self.assertEqual(trial["condition"], "visual_silent")
                self.assertIsNone(trial["audio_wav"])
                self.assertEqual(
                    set(trial["response_choice_ids"]),
                    {item["choice_id"] for item in stimulus["response_choices"]},
                )
                self.assertEqual(
                    trial["estimated_difficulty_score"],
                    stimulus["estimated_difficulty_score"],
                )

            cached_path, cached_manifest = generator.prepare_session(settings, root)
            self.assertEqual(cached_path, path)
            self.assertEqual(cached_manifest, manifest)

    def test_selection_and_foils_do_not_depend_on_progression_or_signal_mode(self):
        families = [
            family for family in self.grammar["sourceFamilies"]
            if family["split"] == "train"
        ]
        seed = 441
        quotas = generator.glyph_count_quotas(14, "automatic", seed)
        lengths = [
            length for length in (1, 2, 3) for _ in range(quotas[length])
        ]
        stream_seed = generator.derive_seed(seed, "selection-v1")
        first_rng = random.Random(stream_seed)
        second_rng = random.Random(stream_seed)
        foil_seed = generator.derive_seed(seed, "foils-v1")
        first_lengths = list(lengths)
        second_lengths = list(lengths)
        first_rng.shuffle(first_lengths)
        second_rng.shuffle(second_lengths)
        first = generator.select_base_trials(
            families, 14, first_rng, first_lengths, random.Random(foil_seed),
        )
        second = generator.select_base_trials(
            families, 14, second_rng, second_lengths, random.Random(foil_seed),
        )
        project = lambda specs: [(
            item["transformationSignature"], item["choiceTargets"]
        ) for item in specs]
        self.assertEqual(project(first), project(second))

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

    def test_aligned_assets_shift_a_complete_target_by_one_dot_without_clipping(self):
        choices = [["c"], ["zero-o"], ["e"], ["g"]]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets = render_trial_images(
                ["c"], ["c"], choices, root, "aligned", seed=20,
                include_aligned_assets=True,
            )
            self.assertEqual(
                abs(assets["aligned_displacement_audio_dx"]),
                ALIGNED_DISPLACEMENT_AUDIO_PIXELS,
            )
            self.assertEqual(assets["aligned_displacement_audio_dy"], 0)
            self.assertEqual(
                assets["aligned_displacement_plate_pixels"],
                ALIGNED_DISPLACEMENT_AUDIO_PIXELS * 4,
            )
            aligned_plate = np.asarray(Image.open(
                root / assets["visual_aligned_plate_png"],
            ))
            canonical_plate = np.asarray(Image.open(
                root / assets["canonical_visual_plate_png"],
            ))
            aligned_input = np.asarray(Image.open(root / assets["aligned_input_png"]))
            background = np.asarray(Image.open(root / assets["background_input_png"]))
            self.assertFalse(np.array_equal(aligned_plate, canonical_plate))
            red_pixels = (
                aligned_plate[:, :, 0].astype(np.int16)
                - aligned_plate[:, :, 1].astype(np.int16) > 70
            )
            yellow_pixels = (
                aligned_plate[:, :, 0].astype(np.int16)
                - aligned_plate[:, :, 2].astype(np.int16) > 100
            ) & (
                aligned_plate[:, :, 1].astype(np.int16)
                - aligned_plate[:, :, 2].astype(np.int16) > 80
            ) & (
                np.abs(
                    aligned_plate[:, :, 0].astype(np.int16)
                    - aligned_plate[:, :, 1].astype(np.int16)
                ) < 80
            )
            self.assertGreater(np.count_nonzero(red_pixels), 0)
            self.assertGreater(np.count_nonzero(yellow_pixels), 0)
            self.assertEqual(aligned_input.shape, (AUDIO_HEIGHT, AUDIO_WIDTH))
            self.assertEqual(
                np.count_nonzero(aligned_input != background),
                assets["aligned_target_pixel_count"],
            )
            self.assertEqual(
                assets["canonical_target_pixel_count"],
                assets["aligned_target_pixel_count"],
            )
            self.assertEqual(
                assets["canonical_target_mask_sha256"],
                assets["aligned_visual_base_mask_sha256"],
            )
            self.assertEqual(
                assets["aligned_target_mask_sha256"],
                assets["aligned_visual_shifted_mask_sha256"],
            )
            self.assertGreater(assets["aligned_visual_overlap_dot_count"], 0)
            self.assertEqual(
                assets["aligned_visual_carrier_version"],
                ALIGNED_VISUAL_CARRIER_VERSION,
            )
            self.assertEqual(
                assets["aligned_visual_pair_axis"], ALIGNED_VISUAL_PAIR_AXIS,
            )
            self.assertEqual(
                assets["aligned_visual_dot_pitch_pixels"],
                ALIGNED_VISUAL_DOT_STEP,
            )
            self.assertEqual(
                assets["aligned_visual_carrier_dot_count"],
                ALIGNED_VISUAL_CARRIER_DOT_COUNT,
            )
            self.assertEqual(
                assets["aligned_visual_carrier_radius_histogram"],
                {
                    "channel_a": ALIGNED_VISUAL_CARRIER_RADIUS_HISTOGRAM,
                    "channel_b": ALIGNED_VISUAL_CARRIER_RADIUS_HISTOGRAM,
                },
            )
            self.assertEqual(
                assets["aligned_visual_carrier_occupied_pixel_count"],
                ALIGNED_VISUAL_CARRIER_OCCUPIED_PIXEL_COUNT,
            )
            self.assertEqual(
                assets["aligned_visual_pair_offset_pixels"],
                ALIGNED_VISUAL_PAIR_OFFSET_PIXELS,
            )
            self.assertEqual(
                assets["aligned_visual_subdot_radii"],
                list(ALIGNED_VISUAL_SUBDOT_RADII),
            )
            self.assertEqual(
                assets["aligned_visual_density_equivalence_version"],
                ALIGNED_VISUAL_DENSITY_EQUIVALENCE_VERSION,
            )
            self.assertEqual(
                assets["aligned_visual_palette_version"],
                ALIGNED_VISUAL_PALETTE_VERSION,
            )
            self.assertEqual(
                assets["visible_base_colours"], [list(SOURCE_COLOURS[0])],
            )
            self.assertEqual(
                assets["aligned_visual_base_colours"],
                [list(SOURCE_COLOURS[0])],
            )
            self.assertEqual(
                assets["aligned_visual_copy_colour"],
                list(ALIGNED_VISUAL_COPY_COLOUR),
            )
            self.assertEqual(
                assets["aligned_visual_subdot_count"],
                assets["aligned_visual_carrier_dot_count"] * 2,
            )
            self.assertEqual(
                assets["canonical_visual_dot_count"],
                assets["aligned_visual_base_dot_count"],
            )
            self.assertEqual(
                assets["aligned_visual_base_dot_count"],
                assets["aligned_visual_shifted_dot_count"],
            )
            self.assertEqual(
                assets["aligned_visual_base_radius_histogram"],
                assets["aligned_visual_shifted_radius_histogram"],
            )
            self.assertEqual(
                assets["aligned_visual_base_radius_area_units"],
                assets["aligned_visual_shifted_radius_area_units"],
            )
            self.assertEqual(
                assets["aligned_visual_base_active_pixel_count"],
                assets["aligned_visual_shifted_active_pixel_count"],
            )
            self.assertEqual(
                assets["balanced_carrier_occupancy_sha256"],
                assets["canonical_carrier_occupancy_sha256"],
            )
            self.assertEqual(
                assets["canonical_carrier_occupancy_sha256"],
                assets["aligned_carrier_occupancy_sha256"],
            )

    def test_three_glyph_aligned_palette_is_rgb_base_with_yellow_copies(self):
        source_ids = ["c", "e", "u"]
        choices = [
            source_ids,
            ["zero-o", "e", "u"],
            ["c", "f", "u"],
            ["c", "e", "q"],
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets = render_trial_images(
                source_ids, source_ids, choices, root, "rgb-aligned", seed=24,
                include_aligned_assets=True,
            )
            canonical = np.asarray(Image.open(
                root / assets["canonical_visual_plate_png"],
            )).astype(np.int16)
            aligned = np.asarray(Image.open(
                root / assets["visual_aligned_plate_png"],
            )).astype(np.int16)

            def colour_masks(image):
                red = (image[:, :, 0] - image[:, :, 1] > 70) & (
                    image[:, :, 0] - image[:, :, 2] > 70
                )
                green = (image[:, :, 1] - image[:, :, 0] > 70) & (
                    image[:, :, 1] - image[:, :, 2] > 45
                )
                blue = (image[:, :, 2] - image[:, :, 0] > 90) & (
                    image[:, :, 2] - image[:, :, 1] > 60
                )
                yellow = (image[:, :, 0] - image[:, :, 2] > 100) & (
                    image[:, :, 1] - image[:, :, 2] > 80
                ) & (np.abs(image[:, :, 0] - image[:, :, 1]) < 80)
                return red, green, blue, yellow

            canonical_masks = colour_masks(canonical)
            aligned_masks = colour_masks(aligned)
            for mask in canonical_masks[:3]:
                self.assertGreater(np.count_nonzero(mask), 0)
            self.assertEqual(np.count_nonzero(canonical_masks[3]), 0)
            for mask in aligned_masks:
                self.assertGreater(np.count_nonzero(mask), 0)
            zones = (
                slice(round(PLATE_WIDTH * 0.10), round(PLATE_WIDTH * 0.38)),
                slice(round(PLATE_WIDTH * 0.36), round(PLATE_WIDTH * 0.64)),
                slice(round(PLATE_WIDTH * 0.62), round(PLATE_WIDTH * 0.90)),
            )
            for position, zone in enumerate(zones):
                self.assertGreater(
                    np.count_nonzero(aligned_masks[position][:, zone]), 0,
                )
                self.assertGreater(
                    np.count_nonzero(aligned_masks[3][:, zone]), 0,
                )
            expected_base = [list(colour) for colour in SOURCE_COLOURS]
            self.assertEqual(assets["visible_base_colours"], expected_base)
            self.assertEqual(assets["aligned_visual_base_colours"], expected_base)
            self.assertEqual(
                assets["aligned_visual_copy_colour"],
                list(ALIGNED_VISUAL_COPY_COLOUR),
            )

    def test_aligned_visual_uses_bijective_complete_subdots_with_exact_density(self):
        dots = plate_renderer.make_aligned_dot_layout()
        target = plate_renderer.draw_geometry_mask(["zero-o"])
        canonical, canonical_stats = plate_renderer._draw_balanced_dyad_plate(
            dots,
            [target],
            (plate_renderer.CANONICAL_TARGET_COLOUR,),
            np.random.default_rng(23),
            shift_audio_dx=ALIGNED_DISPLACEMENT_AUDIO_PIXELS,
        )
        aligned, aligned_stats = plate_renderer._draw_balanced_dyad_plate(
            dots,
            [target],
            (plate_renderer.CANONICAL_TARGET_COLOUR,),
            np.random.default_rng(23),
            shift_audio_dx=ALIGNED_DISPLACEMENT_AUDIO_PIXELS,
            copy_channel_a_to_b=True,
        )
        self.assertEqual(
            canonical_stats["carrier_occupancy_sha256"],
            aligned_stats["carrier_occupancy_sha256"],
        )
        self.assertEqual(
            aligned_stats["channel_a_dot_count"],
            aligned_stats["channel_b_dot_count"],
        )
        self.assertEqual(
            aligned_stats["channel_a_radius_histogram"],
            aligned_stats["channel_b_radius_histogram"],
        )
        self.assertEqual(
            aligned_stats["channel_a_radius_area_units"],
            aligned_stats["channel_b_radius_area_units"],
        )
        self.assertEqual(
            aligned_stats["channel_a_active_pixel_count"],
            aligned_stats["channel_b_active_pixel_count"],
        )
        self.assertFalse(np.array_equal(
            np.asarray(canonical), np.asarray(aligned),
        ))

    def test_difficulty_model_uses_pixels_alternative_foils_and_outcome_space(self):
        family = self.family_by_source["l"]
        choices = [["c"], ["l"], ["u"], ["zero-o"]]
        result = generator.estimate_difficulty(
            [family],
            ["c"],
            choices,
            source_pixel_count=100,
            diagnostic_pixel_count=20,
        )
        components = result["components"]
        self.assertEqual(components["glyph_load"], 0)
        self.assertAlmostEqual(components["diagnostic_subtlety"], 5 / 6, places=6)
        self.assertEqual(
            components["family_ambiguity"],
            round(math.log(family["familySize"]) / math.log(11**3), 6),
        )
        self.assertEqual(result["inputs"]["source_pixel_count"], 100)
        self.assertEqual(result["inputs"]["diagnostic_pixel_count"], 20)
        self.assertEqual(result["inputs"]["outcome_space_size"], family["familySize"])
        self.assertEqual(len(result["inputs"]["alternative_foil_similarities"]), 2)
        expected_score = round(100 * sum(
            generator.DIFFICULTY_COMPONENT_WEIGHTS[name] * value
            for name, value in components.items()
        ), 4)
        self.assertEqual(result["score"], expected_score)

    def test_mixed_assignment_matches_adjacent_ranks_and_balances_strata(self):
        for seed, expected_extra in (
            (40, "visual_background_audio"),
            (41, "ir_audio"),
        ):
            stimuli = [
                self._schedule_stimulus(index, index * 7)
                for index in range(1, 12)
            ]
            for index, stimulus in enumerate(stimuli):
                stimulus["difficulty_rank"] = index + 1
                stimulus["difficulty_stratum"] = (
                    "easy", "moderate", "hard"
                )[min(2, index * 3 // len(stimuli))]
            generator.assign_mixed_conditions(stimuli, seed)

            counts = {
                condition: sum(
                    item["assigned_condition"] == condition for item in stimuli
                )
                for condition in ("visual_background_audio", "ir_audio")
            }
            self.assertEqual(abs(counts["visual_background_audio"] - counts["ir_audio"]), 1)
            self.assertGreater(counts[expected_extra], counts[
                "ir_audio" if expected_extra == "visual_background_audio"
                else "visual_background_audio"
            ])
            self.assertEqual(stimuli[-1]["assigned_condition"], expected_extra)

            for stratum in ("easy", "moderate", "hard"):
                members = [
                    item for item in stimuli
                    if item["difficulty_stratum"] == stratum
                ]
                stratum_counts = [
                    sum(item["assigned_condition"] == condition for item in members)
                    for condition in ("visual_background_audio", "ir_audio")
                ]
                self.assertLessEqual(abs(stratum_counts[0] - stratum_counts[1]), 1)

            match_ids = {
                item["difficulty_match_id"] for item in stimuli
                if item["difficulty_match_id"] is not None
            }
            self.assertEqual(len(match_ids), 5)
            for match_id in match_ids:
                pair = [
                    item for item in stimuli
                    if item["difficulty_match_id"] == match_id
                ]
                self.assertEqual(
                    {item["assigned_condition"] for item in pair},
                    {"visual_background_audio", "ir_audio"},
                )
                self.assertEqual(
                    abs(pair[0]["difficulty_rank"] - pair[1]["difficulty_rank"]),
                    1,
                )
                expected_gap = round(abs(
                    pair[0]["estimated_difficulty_score"]
                    - pair[1]["estimated_difficulty_score"]
                ), 4)
                self.assertEqual(pair[0]["difficulty_match_score_gap"], expected_gap)
                self.assertEqual(pair[1]["difficulty_match_score_gap"], expected_gap)

        glyph_balanced = []
        for glyph_count in (1, 2, 3):
            for offset in range(4):
                index = len(glyph_balanced) + 1
                stimulus = self._schedule_stimulus(index, index * 3 + offset)
                stimulus["source_ids"] = [f"g{glyph_count}"] * glyph_count
                stimulus["changed_count"] = 1 + (offset % glyph_count)
                stimulus["difficulty_rank"] = index
                stimulus["difficulty_stratum"] = (
                    "easy", "moderate", "hard"
                )[min(2, (index - 1) * 3 // 12)]
                glyph_balanced.append(stimulus)
        generator.assign_mixed_conditions(glyph_balanced, 42)
        for glyph_count in (1, 2, 3):
            members = [
                item for item in glyph_balanced
                if len(item["source_ids"]) == glyph_count
            ]
            self.assertEqual(
                sum(item["assigned_condition"] == "visual_background_audio" for item in members),
                2,
            )
            self.assertEqual(
                sum(item["assigned_condition"] == "ir_audio" for item in members),
                2,
            )
        for match_id in {
            item["difficulty_match_id"] for item in glyph_balanced
        }:
            pair = [
                item for item in glyph_balanced
                if item["difficulty_match_id"] == match_id
            ]
            self.assertEqual(len(pair[0]["source_ids"]), len(pair[1]["source_ids"]))

        schedule = generator.make_schedule(
            stimuli,
            {"signalMode": "mixed", "progression": "growing", "seed": 41},
            random.Random(77),
        )
        self.assertEqual(len(schedule), len(stimuli))
        self.assertEqual(
            len({trial["stimulus_id"] for trial in schedule}), len(stimuli),
        )
        self.assertEqual(
            [trial["estimated_difficulty_score"] for trial in schedule],
            sorted(trial["estimated_difficulty_score"] for trial in schedule),
        )
        self.assertTrue(all(trial["pair_id"] is None for trial in schedule))
        for trial in schedule:
            stimulus = next(
                item for item in stimuli
                if item["stimulus_id"] == trial["stimulus_id"]
            )
            self.assertEqual(trial["condition"], stimulus["assigned_condition"])
            self.assertEqual(
                trial["difficulty_match_score_gap"],
                stimulus["difficulty_match_score_gap"],
            )
            if trial["condition"] == "visual_background_audio":
                self.assertEqual(trial["plate_png"], stimulus["visual_plate_png"])
                self.assertEqual(trial["audio_wav"], stimulus["background_wav"])
            else:
                self.assertEqual(trial["plate_png"], stimulus["ir_plate_png"])
                self.assertEqual(trial["audio_wav"], stimulus["ir_probe_wav"])

    def test_four_way_plan_obeys_identity_change_and_split_laws(self):
        plan = generator.plan_session({
            "split": "test",
            "signalMode": "mixed-aligned",
            "baseStimulusCount": 30,
            "mixedConditionRatio": "1:1:1:2",
            "seed": 1729,
        }, grammar=self.grammar)
        self.assertEqual(plan["condition_quotas"], {
            "visual_background_audio": 6,
            "visual_aligned_overlay": 6,
            "visual_aligned_ir_audio": 6,
            "ir_audio": 12,
        })
        self.assertEqual(plan["eligible_by_glyph_count"], {
            1: 30, 2: 900, 3: 27000,
        })
        self.assertTrue(plan["combinatorial_verification"]["verified"])
        self.assertEqual(
            plan["combinatorial_verification"]["eligible_by_glyph_count"]["1"],
            {"identity": 6, "changed": 24, "total": 30},
        )
        one_glyph_identities = []
        for spec in plan["base_specs"]:
            if spec["assignedCondition"] in generator.ALIGNED_IDENTITY_CONDITIONS:
                self.assertEqual(spec["mappingClass"], "identity")
                self.assertEqual(spec["changedCount"], 0)
                self.assertEqual(spec["sourceIds"], spec["targetIds"])
                if len(spec["sourceIds"]) == 1:
                    one_glyph_identities.append(spec["sourceIds"][0])
            else:
                self.assertEqual(spec["mappingClass"], "changed")
                self.assertGreaterEqual(spec["changedCount"], 1)
            self.assertEqual(len({tuple(item) for item in spec["choiceTargets"]}), 4)
        self.assertEqual(set(one_glyph_identities), {
            "gamma", "v", "j", "four", "e", "h",
        })
        self.assertEqual(
            len({item["transformationSignature"] for item in plan["base_specs"]}),
            30,
        )

    def test_glyph_staircase_grows_only_load_and_shuffles_condition_nature(self):
        condition_pattern = [
            "visual_background_audio",
            "visual_aligned_overlay",
            "visual_aligned_ir_audio",
            "ir_audio",
            "ir_audio",
        ]
        stimuli = []
        assigned_by_id = {}
        glyph_by_id = {}
        for glyph_count in (1, 2, 3):
            for position, condition in enumerate(condition_pattern, start=1):
                index = (glyph_count - 1) * len(condition_pattern) + position
                stimulus = self._schedule_stimulus(index, 100 - index)
                stimulus.update({
                    "source_ids": [f"source-{index}"] * glyph_count,
                    "assigned_condition": condition,
                    "canonical_visual_plate_png": f"canonical-{index}.png",
                    "balanced_carrier_ir_plate_png": f"carrier-{index}.png",
                    "visual_aligned_plate_png": f"aligned-{index}.png",
                    "aligned_target_wav": f"aligned-{index}.wav",
                })
                stimuli.append(stimulus)
                assigned_by_id[stimulus["stimulus_id"]] = condition
                glyph_by_id[stimulus["stimulus_id"]] = glyph_count

        settings = {
            "signalMode": "mixed-aligned",
            "progression": "glyph-growing",
        }
        first = generator.make_schedule(stimuli, settings, random.Random(902))
        repeated = generator.make_schedule(stimuli, settings, random.Random(902))
        second = generator.make_schedule(stimuli, settings, random.Random(903))

        first_ids = [trial["stimulus_id"] for trial in first]
        self.assertEqual(
            first_ids,
            [trial["stimulus_id"] for trial in repeated],
        )
        self.assertNotEqual(
            first_ids,
            [trial["stimulus_id"] for trial in second],
        )
        self.assertNotEqual(
            [trial["condition"] for trial in first],
            [trial["condition"] for trial in second],
        )
        self.assertEqual(set(first_ids), set(glyph_by_id))
        self.assertEqual(
            [glyph_by_id[stimulus_id] for stimulus_id in first_ids],
            [1] * 5 + [2] * 5 + [3] * 5,
        )
        self.assertNotEqual(
            [trial["estimated_difficulty_score"] for trial in first],
            sorted(trial["estimated_difficulty_score"] for trial in first),
        )
        for glyph_count in (1, 2, 3):
            tier_conditions = [
                trial["condition"]
                for trial in first
                if glyph_by_id[trial["stimulus_id"]] == glyph_count
            ]
            self.assertEqual(
                {condition: tier_conditions.count(condition)
                 for condition in generator.ALIGNED_MIXED_CONDITIONS},
                {
                    "visual_background_audio": 1,
                    "visual_aligned_overlay": 1,
                    "visual_aligned_ir_audio": 1,
                    "ir_audio": 2,
                },
            )
        self.assertTrue(all(
            trial["condition"] == assigned_by_id[trial["stimulus_id"]]
            for trial in first
        ))

    def test_paired_schedule_counterbalances_separates_and_reshuffles_choices(self):
        stimuli = [self._schedule_stimulus(index, score) for index, score in enumerate(
            (72, 18, 55, 36, 91), start=1,
        )]
        schedule = generator.make_schedule(
            stimuli,
            {"signalMode": "paired", "progression": "growing"},
            random.Random(8),
        )
        self.assertEqual(len(schedule), 10)
        self.assertTrue(all(item["pair_pass"] == 1 for item in schedule[:5]))
        self.assertTrue(all(item["pair_pass"] == 2 for item in schedule[5:]))
        first_pass_ids = [item["stimulus_id"] for item in schedule[:5]]
        second_pass_ids = [item["stimulus_id"] for item in schedule[5:]]
        self.assertEqual(first_pass_ids, second_pass_ids)
        self.assertEqual(
            [item["estimated_difficulty_score"] for item in schedule[:5]],
            sorted(item["estimated_difficulty_score"] for item in schedule[:5]),
        )
        self.assertLessEqual(
            abs(sum(item["condition"] == "visual_background_audio" for item in schedule[:5])
                - sum(item["condition"] == "ir_audio" for item in schedule[:5])),
            1,
        )
        by_stimulus = {}
        for trial in schedule:
            by_stimulus.setdefault(trial["stimulus_id"], []).append(trial)
        for trials in by_stimulus.values():
            self.assertEqual({item["condition"] for item in trials}, {
                "visual_background_audio", "ir_audio",
            })
            self.assertEqual({item["pair_order"] for item in trials}, {
                "visual-ir" if trials[0]["condition"] == "visual_background_audio" else "ir-visual"
            })
            self.assertGreaterEqual(trials[0]["pair_lag"], 2)
            self.assertEqual(trials[0]["pair_lag"], trials[1]["pair_lag"])
            self.assertNotEqual(
                trials[0]["response_choice_ids"], trials[1]["response_choice_ids"],
            )
            visible = next(
                item for item in trials
                if item["condition"] == "visual_background_audio"
            )
            ir_trial = next(item for item in trials if item["condition"] == "ir_audio")
            self.assertEqual(visible["audio_content"], "background-only-carrier")
            self.assertEqual(
                ir_trial["audio_content"],
                "diagnostic-ir-probe-plus-background-carrier",
            )

    def test_mixed_progression_independently_reorders_second_pair_pass(self):
        stimuli = [self._schedule_stimulus(index, index) for index in range(1, 9)]
        schedule = generator.make_schedule(
            stimuli,
            {"signalMode": "paired", "progression": "mixed"},
            random.Random(51),
        )
        first = [item["stimulus_id"] for item in schedule[:8]]
        second = [item["stimulus_id"] for item in schedule[8:]]
        self.assertNotEqual(first, second)
        self.assertEqual(set(first), set(second))
        self.assertGreaterEqual(min(item["pair_lag"] for item in schedule), 2)

    @patch.object(generator, "generate_soundscape")
    def test_ir_mixed_and_paired_audio_modes_share_carrier_gain(self, generate):
        generate.side_effect = self._write_fake_soundscape
        common = {
            "split": "train",
            "baseStimulusCount": 4,
            "glyphComposition": "1",
            "progression": "growing",
            "feedbackEnabled": True,
            "seed": 602,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ir_path, ir_manifest = generator.prepare_session(
                {**common, "signalMode": "ir"}, root,
            )
            self.assertTrue(ir_manifest["audio_generated"])
            self.assertEqual(
                ir_manifest["audio_normalization_method"],
                AUDIO_NORMALIZATION_METHOD,
            )
            self.assertEqual(
                ir_manifest["audio_render_version"], generator.AUDIO_RENDER_VERSION,
            )
            self.assertEqual(ir_manifest["audio_peak_ceiling_dbfs"], PEAK_CEILING_DBFS)
            self.assertFalse(ir_manifest["audio_whole_file_rms_equalized"])
            self.assertTrue(ir_manifest["audio_counterfactual_shared_gain"])
            self.assertEqual(ir_manifest["condition_distribution"], {"ir_audio": 4})
            for stimulus in ir_manifest["stimuli"]:
                self.assertIsNotNone(stimulus["ir_probe_wav"])
                self.assertIsNone(stimulus["background_wav"])
                normalization = stimulus["audio_normalization"]
                self.assertAlmostEqual(normalization["shared_gain_linear"], 0.925)
                self.assertFalse(normalization["peak_limited"])
                self.assertFalse(
                    normalization["counterfactuals"]["background_carrier"]["retained"]
                )
                self.assertTrue(
                    normalization["counterfactuals"]["ir_probe"]["retained"]
                )
                self.assertAlmostEqual(
                    wav_rms_int16(ir_path.parent / stimulus["ir_probe_wav"]),
                    1_665,
                )
            for trial in ir_manifest["trials"]:
                stimulus = next(
                    item for item in ir_manifest["stimuli"]
                    if item["stimulus_id"] == trial["stimulus_id"]
                )
                self.assertEqual(trial["condition"], "ir_audio")
                self.assertEqual(trial["plate_png"], stimulus["ir_plate_png"])

            mixed_path, mixed_manifest = generator.prepare_session(
                {
                    **common,
                    "signalMode": "mixed",
                    "baseStimulusCount": 5,
                },
                root,
            )
            self.assertEqual(mixed_manifest["base_stimulus_count"], 5)
            self.assertEqual(mixed_manifest["total_presentation_count"], 5)
            self.assertEqual(mixed_manifest["comparison_design"], (
                "distinct-stimulus-carrier-controlled-between-condition"
            ))
            self.assertFalse(mixed_manifest["stimuli_repeated_across_conditions"])
            self.assertEqual(mixed_manifest["condition_distribution"], {
                "visual_background_audio": 3, "ir_audio": 2,
            })
            assignment = mixed_manifest["condition_assignment"]
            self.assertEqual(assignment["global_absolute_difference"], 1)
            self.assertEqual(assignment["complete_difficulty_match_pairs"], 2)
            self.assertEqual(assignment["unmatched_remainder_count"], 1)
            self.assertTrue(assignment["all_complete_pairs_cross_condition"])
            self.assertLessEqual(
                assignment["by_glyph_count"]["1"]["absolute_difference"], 1,
            )
            self.assertEqual(
                len({trial["stimulus_id"] for trial in mixed_manifest["trials"]}),
                5,
            )
            for stimulus in mixed_manifest["stimuli"]:
                generated_paths = [
                    stimulus[key]
                    for key in ("ir_probe_wav", "background_wav")
                    if stimulus[key] is not None
                ]
                self.assertEqual(len(generated_paths), 1)
                normalization = stimulus["audio_normalization"]
                self.assertAlmostEqual(normalization["shared_gain_linear"], 0.925)
                if stimulus["assigned_condition"] == "ir_audio":
                    self.assertIsNotNone(stimulus["ir_probe_wav"])
                    self.assertIsNone(stimulus["background_wav"])
                    self.assertIn("ir_probe", normalization["counterfactuals"])
                    expected_rms = 1_665
                else:
                    self.assertIsNone(stimulus["ir_probe_wav"])
                    self.assertIsNotNone(stimulus["background_wav"])
                    self.assertNotIn("ir_probe", normalization["counterfactuals"])
                    expected_rms = CARRIER_TARGET_RMS_INT16
                self.assertAlmostEqual(
                    wav_rms_int16(mixed_path.parent / generated_paths[0]),
                    expected_rms,
                )
            for trial in mixed_manifest["trials"]:
                stimulus = next(
                    item for item in mixed_manifest["stimuli"]
                    if item["stimulus_id"] == trial["stimulus_id"]
                )
                self.assertEqual(trial["condition"], stimulus["assigned_condition"])
                self.assertEqual(
                    trial["difficulty_match_score_gap"],
                    stimulus["difficulty_match_score_gap"],
                )

            original_audio = mixed_manifest["trials"][0]["audio_wav"]
            mixed_manifest["trials"][0]["audio_wav"] = "audio/wrong.wav"
            self.assertFalse(generator.manifest_is_complete(
                mixed_manifest, mixed_path.parent,
            ))
            mixed_manifest["trials"][0]["audio_wav"] = original_audio
            self.assertTrue(generator.manifest_is_complete(
                mixed_manifest, mixed_path.parent,
            ))

            aligned_path, aligned_manifest = generator.prepare_session(
                {
                    **common,
                    "signalMode": "mixed-aligned",
                    "baseStimulusCount": 10,
                    "mixedConditionRatio": "1:1:1:2",
                    "progression": "glyph-growing",
                },
                root,
            )
            glyph_count_by_stimulus = {
                stimulus["stimulus_id"]: len(stimulus["source_ids"])
                for stimulus in aligned_manifest["stimuli"]
            }
            aligned_glyph_order = [
                glyph_count_by_stimulus[trial["stimulus_id"]]
                for trial in aligned_manifest["trials"]
            ]
            self.assertEqual(aligned_glyph_order, sorted(aligned_glyph_order))
            self.assertEqual(aligned_manifest["condition_distribution"], {
                "visual_background_audio": 2,
                "visual_aligned_overlay": 2,
                "visual_aligned_ir_audio": 2,
                "ir_audio": 4,
            })
            self.assertEqual(
                aligned_manifest["condition_assignment"]["condition_ratio"],
                "1:1:1:2",
            )
            self.assertTrue(
                aligned_manifest["combinatorial_verification"]["verified"]
            )
            self.assertEqual(
                aligned_manifest["combinatorial_verification"]
                ["identity_density_balance"]["load_basis"],
                "rendered_signal_dot_count",
            )
            self.assertEqual(len({
                stimulus["aligned_visual_carrier_occupied_pixel_count"]
                for stimulus in aligned_manifest["stimuli"]
            }), 1)
            signal_density = aligned_manifest["condition_assignment"][
                "visible_signal_density_balance"
            ]
            self.assertTrue(signal_density["carrier_density_is_fixed_separately"])
            self.assertEqual(
                set(signal_density["by_condition"]),
                set(generator.ALIGNED_MIXED_CONDITIONS),
            )
            for stimulus in aligned_manifest["stimuli"]:
                condition = stimulus["assigned_condition"]
                expected_base_colours = [
                    list(colour)
                    for colour in SOURCE_COLOURS[:len(stimulus["source_ids"])]
                ]
                self.assertEqual(
                    stimulus["aligned_visual_palette_version"],
                    ALIGNED_VISUAL_PALETTE_VERSION,
                )
                self.assertEqual(
                    stimulus["visible_base_colours"], expected_base_colours,
                )
                if condition in generator.ALIGNED_IDENTITY_CONDITIONS:
                    self.assertEqual(stimulus["mapping_class"], "identity")
                    self.assertEqual(stimulus["source_ids"], stimulus["target_ids"])
                    self.assertIsNone(stimulus["decoy_choice_id"])
                    self.assertTrue(
                        (
                            aligned_path.parent
                            / stimulus["canonical_visual_plate_png"]
                        ).is_file()
                    )
                    self.assertTrue(
                        (
                            aligned_path.parent
                            / stimulus["visual_aligned_plate_png"]
                        ).is_file()
                    )
                    self.assertTrue(
                        (aligned_path.parent / stimulus["aligned_input_png"]).is_file()
                    )
                    self.assertEqual(
                        stimulus["canonical_target_mask_sha256"],
                        stimulus["aligned_visual_base_mask_sha256"],
                    )
                    self.assertEqual(
                        stimulus["aligned_target_mask_sha256"],
                        stimulus["aligned_visual_shifted_mask_sha256"],
                    )
                    self.assertEqual(
                        stimulus["aligned_visual_carrier_version"],
                        ALIGNED_VISUAL_CARRIER_VERSION,
                    )
                    self.assertEqual(
                        stimulus["aligned_visual_base_colours"],
                        expected_base_colours,
                    )
                    self.assertEqual(
                        stimulus["aligned_visual_copy_colour"],
                        list(ALIGNED_VISUAL_COPY_COLOUR),
                    )
                    self.assertEqual(
                        stimulus["aligned_visual_subdot_count"],
                        stimulus["aligned_visual_carrier_dot_count"] * 2,
                    )
                    self.assertEqual(
                        stimulus["canonical_visual_dot_count"],
                        stimulus["visible_signal_dot_count"],
                    )
                else:
                    self.assertEqual(stimulus["mapping_class"], "changed")
                    self.assertGreaterEqual(stimulus["changed_count"], 1)
                if condition == "visual_aligned_ir_audio":
                    self.assertIsNotNone(stimulus["aligned_target_wav"])
                    self.assertIsNone(stimulus["background_wav"])
                    self.assertIn(
                        "aligned_target",
                        stimulus["audio_normalization"]["counterfactuals"],
                    )
                elif condition in {
                    "visual_background_audio", "visual_aligned_overlay",
                }:
                    self.assertIsNotNone(stimulus["background_wav"])
                    self.assertIsNone(stimulus["aligned_target_wav"])
                else:
                    self.assertIsNotNone(stimulus["ir_probe_wav"])
            self.assertTrue(generator.manifest_is_complete(
                aligned_manifest, aligned_path.parent,
            ))
            identity_stimulus = next(
                item for item in aligned_manifest["stimuli"]
                if item["assigned_condition"] in generator.ALIGNED_IDENTITY_CONDITIONS
            )
            original_digest = identity_stimulus["aligned_visual_base_mask_sha256"]
            identity_stimulus["aligned_visual_base_mask_sha256"] = "0" * 64
            self.assertFalse(generator.manifest_is_complete(
                aligned_manifest, aligned_path.parent,
            ))
            identity_stimulus["aligned_visual_base_mask_sha256"] = original_digest
            original_carrier = identity_stimulus["aligned_visual_carrier_version"]
            identity_stimulus["aligned_visual_carrier_version"] = "split-halves-v0"
            self.assertFalse(generator.manifest_is_complete(
                aligned_manifest, aligned_path.parent,
            ))
            identity_stimulus["aligned_visual_carrier_version"] = original_carrier
            original_pixels = identity_stimulus[
                "aligned_visual_carrier_occupied_pixel_count"
            ]
            identity_stimulus[
                "aligned_visual_carrier_occupied_pixel_count"
            ] = original_pixels - 1
            self.assertFalse(generator.manifest_is_complete(
                aligned_manifest, aligned_path.parent,
            ))
            identity_stimulus[
                "aligned_visual_carrier_occupied_pixel_count"
            ] = original_pixels
            original_palette = identity_stimulus["aligned_visual_palette_version"]
            identity_stimulus["aligned_visual_palette_version"] = "cyan-copy-v0"
            self.assertFalse(generator.manifest_is_complete(
                aligned_manifest, aligned_path.parent,
            ))
            identity_stimulus["aligned_visual_palette_version"] = original_palette
            complementary = next(
                item for item in aligned_manifest["stimuli"]
                if item["assigned_condition"] == "ir_audio"
            )
            original_class = complementary["mapping_class"]
            complementary["mapping_class"] = "identity"
            self.assertFalse(generator.manifest_is_complete(
                aligned_manifest, aligned_path.parent,
            ))
            complementary["mapping_class"] = original_class
            self.assertTrue(generator.manifest_is_complete(
                aligned_manifest, aligned_path.parent,
            ))

            paired_path, paired_manifest = generator.prepare_session(
                {**common, "signalMode": "paired"}, root,
            )
            self.assertEqual(paired_manifest["total_presentation_count"], 8)
            self.assertEqual(paired_manifest["condition_distribution"], {
                "visual_background_audio": 4, "ir_audio": 4,
            })
            stimulus_projection = lambda manifest: [(
                item["source_ids"],
                item["target_ids"],
                [choice["target_ids"] for choice in item["response_choices"]],
                item["estimated_difficulty_score"],
            ) for item in manifest["stimuli"]]
            self.assertEqual(
                stimulus_projection(ir_manifest),
                stimulus_projection(paired_manifest),
            )
            for stimulus in paired_manifest["stimuli"]:
                self.assertIsNotNone(stimulus["ir_probe_wav"])
                self.assertIsNotNone(stimulus["background_wav"])
                values = [
                    wav_rms_int16(paired_path.parent / stimulus[key])
                    for key in ("ir_probe_wav", "background_wav")
                ]
                self.assertAlmostEqual(values[0], 1_665)
                self.assertAlmostEqual(values[1], CARRIER_TARGET_RMS_INT16)
                self.assertAlmostEqual(values[0] / values[1], 9.0)
                normalization = stimulus["audio_normalization"]
                self.assertAlmostEqual(normalization["shared_gain_linear"], 0.925)
                self.assertTrue(all(
                    metrics["retained"]
                    for metrics in normalization["counterfactuals"].values()
                ))

            # A valid WAV that differs from its recorded post-gain metrics is stale.
            probe_path = ir_path.parent / ir_manifest["stimuli"][0]["ir_probe_wav"]
            self._write_wav(probe_path, amplitude=300)
            self.assertFalse(generator.manifest_is_complete(ir_manifest, ir_path.parent))

    def test_carrier_reference_applies_one_gain_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            carrier = root / "carrier.wav"
            probe = root / "probe.wav"
            self._write_wav(carrier, 200)
            self._write_wav(probe, 1_800)
            measured = apply_carrier_referenced_gain(
                (carrier, probe), carrier,
            )
            self.assertEqual(measured["method"], AUDIO_NORMALIZATION_METHOD)
            self.assertAlmostEqual(measured["requested_gain_linear"], 0.925)
            self.assertAlmostEqual(measured["shared_gain_linear"], 0.925)
            self.assertFalse(measured["peak_limited"])
            self.assertAlmostEqual(wav_rms_int16(carrier), CARRIER_TARGET_RMS_INT16)
            self.assertAlmostEqual(wav_rms_int16(probe), 1_665)
            self.assertAlmostEqual(
                wav_rms_int16(probe) / wav_rms_int16(carrier), 9.0,
            )
            self.assertEqual(set(measured["files"]), {carrier.name, probe.name})
            for path in (carrier, probe):
                with wave.open(str(path), "rb") as wav_file:
                    self.assertEqual(wav_file.getparams()[:4], (2, 2, 48_000, 50_400))

    def test_carrier_reference_peak_limits_the_shared_gain_without_clipping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            carrier = root / "carrier.wav"
            probe = root / "probe.wav"
            self._write_wav(carrier, 100)
            self._write_wav(probe, -32_768)
            measured = apply_carrier_referenced_gain((carrier, probe), carrier)
            self.assertTrue(measured["peak_limited"])
            self.assertLess(
                measured["shared_gain_linear"],
                measured["requested_gain_linear"],
            )
            self.assertLess(wav_rms_int16(carrier), CARRIER_TARGET_RMS_INT16)
            self.assertLessEqual(
                wav_peak_int16(probe), measured["peak_ceiling_int16"],
            )
            self.assertEqual(
                measured["files"][probe.name]["raw_peak_int16"], 32_768,
            )

    def test_carrier_reference_rejects_a_silent_carrier(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            carrier = root / "carrier.wav"
            probe = root / "probe.wav"
            self._write_wav(carrier, 0)
            self._write_wav(probe, 1_800)
            with self.assertRaisesRegex(RuntimeError, "silent soundscape"):
                apply_carrier_referenced_gain((carrier, probe), carrier)

    def test_validate_wav_requires_the_full_pcm_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "truncated.wav"
            self._write_wav(path, 200)
            with path.open("r+b") as wav_file:
                wav_file.truncate(44 + 1_013 * 2 * 2)
            with self.assertRaisesRegex(
                RuntimeError, "incomplete soundscape payload",
            ):
                soundscape.validate_wav(path)

    def test_soundscape_waits_for_full_wav_then_atomically_promotes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "probe.wav"
            processes = []

            class CompletingProcess:
                def __init__(inner_self, command, stderr):
                    inner_self.partial = Path(
                        command[command.index("-o") + 1]
                    )
                    inner_self.stderr = stderr
                    inner_self.returncode = None
                    inner_self.poll_count = 0
                    inner_self.terminated = False

                def poll(inner_self):
                    inner_self.poll_count += 1
                    if inner_self.poll_count == 1:
                        self._write_wav(
                            inner_self.partial, 200, frame_count=1_013,
                        )
                    elif inner_self.poll_count == 2:
                        self._write_wav(inner_self.partial, 200)
                    return inner_self.returncode

                def terminate(inner_self):
                    # Publication must happen only after the complete private
                    # attempt has survived process shutdown and revalidation.
                    soundscape.validate_wav(inner_self.partial)
                    self.assertFalse(destination.exists())
                    inner_self.terminated = True
                    inner_self.returncode = -15

                def wait(inner_self, timeout=None):
                    self.assertIsNotNone(inner_self.returncode)
                    return inner_self.returncode

                def kill(inner_self):
                    inner_self.returncode = -9

            def fake_popen(command, **kwargs):
                self.assertTrue(kwargs["start_new_session"])
                process = CompletingProcess(command, kwargs["stderr"])
                processes.append(process)
                return process

            with (
                patch.object(soundscape.subprocess, "Popen", side_effect=fake_popen),
                patch.object(soundscape.time, "sleep", return_value=None),
            ):
                soundscape._generate_once(
                    root / "input.png",
                    destination,
                    root / "raspivoice",
                    root,
                )

            self.assertEqual(len(processes), 1)
            process = processes[0]
            self.assertTrue(process.terminated)
            self.assertGreaterEqual(process.poll_count, 3)
            self.assertNotEqual(process.partial, destination)
            self.assertTrue(process.partial.name.endswith(".partial.wav"))
            self.assertFalse(process.partial.exists())
            soundscape.validate_wav(destination)

    def test_soundscape_accepts_status_one_after_complete_frame(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "probe.wav"
            processes = []

            class CompletedProcess:
                def __init__(inner_self, command, stderr):
                    inner_self.partial = Path(
                        command[command.index("-o") + 1]
                    )
                    self._write_wav(inner_self.partial, 200)
                    inner_self.poll_count = 0

                def poll(inner_self):
                    inner_self.poll_count += 1
                    return 1

                def terminate(inner_self):
                    self.fail("an exited process must not be terminated")

                def wait(inner_self, timeout=None):
                    return 1

                def kill(inner_self):
                    self.fail("an exited process must not be killed")

            def fake_popen(command, **kwargs):
                process = CompletedProcess(command, kwargs["stderr"])
                processes.append(process)
                return process

            with patch.object(soundscape.subprocess, "Popen", side_effect=fake_popen):
                soundscape._generate_once(
                    root / "input.png",
                    destination,
                    root / "raspivoice",
                    root,
                )

            process = processes[0]
            self.assertEqual(process.poll_count, 1)
            self.assertFalse(process.partial.exists())
            soundscape.validate_wav(destination)

    def test_soundscape_enospc_reaps_child_and_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "probe.wav"
            processes = []

            class CompleteRunningProcess:
                def __init__(inner_self, command):
                    inner_self.partial = Path(
                        command[command.index("-o") + 1]
                    )
                    self._write_wav(inner_self.partial, 200)
                    inner_self.returncode = None
                    inner_self.terminated = False
                    inner_self.waited = False

                def poll(inner_self):
                    return inner_self.returncode

                def terminate(inner_self):
                    inner_self.terminated = True
                    inner_self.returncode = -15

                def wait(inner_self, timeout=None):
                    inner_self.waited = True
                    return inner_self.returncode

                def kill(inner_self):
                    inner_self.returncode = -9

            def fake_popen(command, **_kwargs):
                process = CompleteRunningProcess(command)
                processes.append(process)
                return process

            with (
                patch.object(soundscape.subprocess, "Popen", side_effect=fake_popen),
                patch.object(
                    soundscape.os,
                    "replace",
                    side_effect=OSError(28, "No space left on device"),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "soundscape I/O failed.*No space left on device",
                ):
                    soundscape._generate_once(
                        root / "input.png",
                        destination,
                        root / "raspivoice",
                        root,
                    )

            process = processes[0]
            self.assertTrue(process.terminated)
            self.assertTrue(process.waited)
            self.assertFalse(process.partial.exists())
            self.assertFalse(destination.exists())

    def test_soundscape_timeout_kills_process_and_cleans_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "probe.wav"
            processes = []

            class StuckProcess:
                def __init__(inner_self, command, stderr):
                    inner_self.partial = Path(
                        command[command.index("-o") + 1]
                    )
                    inner_self.returncode = None
                    inner_self.terminated = False
                    inner_self.killed = False
                    stderr.write(b"Cannot open screen.\n")
                    stderr.flush()

                def poll(inner_self):
                    return inner_self.returncode

                def terminate(inner_self):
                    inner_self.terminated = True

                def wait(inner_self, timeout=None):
                    if inner_self.returncode is None:
                        raise subprocess.TimeoutExpired("raspivoice", timeout)
                    return inner_self.returncode

                def kill(inner_self):
                    inner_self.killed = True
                    inner_self.returncode = -9

            def fake_popen(command, **kwargs):
                process = StuckProcess(command, kwargs["stderr"])
                processes.append(process)
                return process

            with (
                patch.object(soundscape.subprocess, "Popen", side_effect=fake_popen),
                patch.object(soundscape, "RASPIVOICE_MAX_WAIT_S", 0),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "timed out.*Cannot open screen",
                ):
                    soundscape._generate_once(
                        root / "input.png",
                        destination,
                        root / "raspivoice",
                        root,
                    )

            process = processes[0]
            self.assertTrue(process.terminated)
            self.assertTrue(process.killed)
            self.assertFalse(process.partial.exists())
            self.assertFalse(destination.exists())

    def test_soundscape_nonzero_exit_preserves_destination_and_cleans_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "probe.wav"
            destination.write_bytes(b"existing destination")
            processes = []

            class FailedProcess:
                def __init__(inner_self, command, stderr):
                    inner_self.partial = Path(
                        command[command.index("-o") + 1]
                    )
                    inner_self.returncode = None
                    inner_self.polled = False
                    inner_self.stderr = stderr

                def poll(inner_self):
                    if not inner_self.polled:
                        inner_self.polled = True
                        self._write_wav(
                            inner_self.partial, 200, frame_count=1_013,
                        )
                        inner_self.stderr.write(b"encoder failed\n")
                        inner_self.stderr.flush()
                        inner_self.returncode = 7
                    return inner_self.returncode

                def terminate(inner_self):
                    self.fail("an exited process must not be terminated")

                def wait(inner_self, timeout=None):
                    return inner_self.returncode

                def kill(inner_self):
                    self.fail("an exited process must not be killed")

            def fake_popen(command, **kwargs):
                process = FailedProcess(command, kwargs["stderr"])
                processes.append(process)
                return process

            with patch.object(
                soundscape.subprocess, "Popen", side_effect=fake_popen,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "status 7.*encoder failed",
                ):
                    soundscape._generate_once(
                        root / "input.png",
                        destination,
                        root / "raspivoice",
                        root,
                    )

            process = processes[0]
            self.assertFalse(process.partial.exists())
            self.assertEqual(destination.read_bytes(), b"existing destination")

    @staticmethod
    def _schedule_stimulus(index, score):
        choice_ids = [f"s-{index}-choice-{position}" for position in range(1, 5)]
        return {
            "stimulus_id": f"s-{index}",
            "ir_plate_png": f"ir-{index}.png",
            "visual_plate_png": f"visual-{index}.png",
            "ir_probe_wav": f"probe-{index}.wav",
            "background_wav": f"background-{index}.wav",
            "response_choices": [{"choice_id": choice_id} for choice_id in choice_ids],
            "target_choice_id": choice_ids[0],
            "decoy_choice_id": choice_ids[1],
            "transformation_signature": f"source-{index}--target-{index}",
            "mapping_repetition_index": 1,
            "estimated_difficulty_score": score,
            "difficulty_components": {
                name: score / 100 for name in generator.DIFFICULTY_COMPONENT_NAMES
            },
            "difficulty_model_version": generator.DIFFICULTY_MODEL_VERSION,
            "difficulty_rank": index,
            "difficulty_stratum": "moderate",
        }

    @classmethod
    def _write_fake_soundscape(cls, _png_path, wav_path, *_args, **_kwargs):
        amplitude = (
            1_800
            if "probe" in wav_path.name or "aligned_target" in wav_path.name
            else 200
        )
        cls._write_wav(wav_path, amplitude)

    @staticmethod
    def _write_wav(path, amplitude, frame_count=EXPECTED_WAV_FRAMES):
        samples = np.full(frame_count * 2, amplitude, dtype="<i2")
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48_000)
            wav_file.writeframes(samples.tobytes())


if __name__ == "__main__":
    unittest.main()
