import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, _tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])


class WebStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "ishihara" / "index.html").read_text()
        cls.javascript = (ROOT / "ishihara" / "app.js").read_text()

    def test_every_static_dom_lookup_has_a_matching_element(self):
        parser = IdCollector()
        parser.feed(self.html)
        looked_up = set(re.findall(r"getElementById\(['\"]([^'\"]+)", self.javascript))
        self.assertEqual(looked_up - parser.ids, set())

    def test_recenter_gate_rejects_keyboard_activation(self):
        self.assertIn("event.detail === 0", self.javascript)
        self.assertNotIn("event.code === 'Space'", self.javascript)

    def test_data_collection_defaults_are_controlled(self):
        self.assertRegex(
            self.html,
            r'<option value="fullscreen-calibrated">Calibrated apparent size',
        )
        self.assertIn('<option value="fullscreen-expanded">', self.html)
        self.assertRegex(
            self.html,
            r'<option value="windowed">Compact native size',
        )
        self.assertRegex(
            self.html,
            r'<option value="keyboard">Keyboard keys 1–4 \(recommended\)',
        )
        self.assertIn("Fullscreen data collection requires display ID", self.javascript)
        self.assertIn('id="target-angle-deg"', self.html)
        self.assertIn("calibratedStageSize", self.javascript)
        self.assertRegex(
            self.html,
            r'<input id="num-trials" type="number" min="4" value="32">',
        )
        self.assertIn('id="session-preset"', self.html)
        self.assertIn('id="calibration-fields" class="hidden"', self.html)
        self.assertIn('Advanced research controls', self.html)
        self.assertIn("document.getElementById('split').value", self.javascript)
        self.assertIn("document.getElementById('mode').value", self.javascript)

    def test_timing_and_geometry_are_auditable(self):
        for field in (
            "rt_choice_onset_ms",
            "rt_stimulus_onset_ms",
            "stage_width_visual_angle_deg",
            "stage_height_visual_angle_deg",
            "target_stage_width_visual_angle_deg",
            "stage_width_visual_angle_error_deg",
            "presentation_scale_mode",
            "css_px_per_audio_column",
            "css_px_per_audio_row",
            "stimulus_width_css_px",
            "stimulus_height_css_px",
            "display_coordinate_mapping",
            "display_axis_stretch_y_over_x",
            "visual_to_audio_scale_x",
            "coordinate_mapping",
            "visual_presentation",
            "audio_presentation",
            "audio_content",
            "stimulus_duration_actual_ms",
            "audio_sweeps_planned",
            "audio_sweeps_completed",
            "static_visual_duration_planned_ms",
            "static_visual_onset_frame_offset_ms",
            "mask_duration_actual_ms",
        ):
            self.assertIn(field, self.javascript)
        self.assertIn("audioContextTimeToPerformanceMs", self.javascript)
        self.assertIn("audioSweepConfig", self.javascript)
        self.assertIn("const responseOnsetMs = await nextFrame()", self.javascript)

    def test_boot_is_cache_versioned_and_failure_is_visible(self):
        self.assertIn('app.js?v=simple-ui-2', self.html)
        self.assertIn('id="boot-error"', self.html)
        self.assertIn(
            "dataset.ishiharaAppVersion = 'simple-ui-2'",
            self.javascript,
        )

    def test_rgb_is_static_while_three_audio_sweeps_share_one_timeline(self):
        self.assertIn("const AUDIO_SWEEP_REPETITIONS = 3", self.javascript)
        self.assertIn("const INTER_SWEEP_INTERVAL_MS = 250", self.javascript)
        self.assertIn("startStaticVisualPresentation", self.javascript)
        self.assertIn("drawStaticStimulus", self.javascript)
        self.assertNotIn("drawSweepColumn", self.javascript)
        self.assertIn("source.start(contextStart)", self.javascript)
        self.assertIn("return trial.stimulus.ir_background_wav", self.javascript)
        self.assertIn("background-only-ir-carrier", self.javascript)
        self.assertIn("RGB plate remains static", self.html)

    def test_conditions_express_composites_and_single_component_controls(self):
        for value in (
            "visual-composite", "ir-composite", "visible-only", "ir-only", "ir-scrambled",
        ):
            self.assertIn(f'<option value="{value}">', self.html)
        for value in ("r-ir", "g-ir", "rg-ir", "rgb-ir"):
            self.assertIn(f'<option value="{value}">', self.html)
        self.assertIn('id="complexity"', self.html)
        self.assertIn('id="channel-recipe"', self.html)
        self.assertIn("no universal “choose the option with more strokes” rule", self.html)
        self.assertIn("diagnostic_dot_count", self.javascript)
        self.assertIn("decoy_selected", self.javascript)
        self.assertIn("choice_structure", self.javascript)
        self.assertIn("probe_state", self.javascript)
        self.assertIn("visible_probe_channel", self.javascript)
        self.assertIn("visual_composite_png", self.javascript)
        self.assertIn("visible_components_png", self.javascript)
        self.assertIn("neutral_plate_png", self.javascript)


if __name__ == "__main__":
    unittest.main()
