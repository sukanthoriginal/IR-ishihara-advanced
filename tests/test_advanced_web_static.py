import os
import plistlib
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
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.add(attributes["id"])


class AdvancedWebStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "advanced" / "index.html").read_text()
        cls.javascript = (ROOT / "advanced" / "app.js").read_text()

    def test_every_dom_lookup_has_a_matching_element(self):
        parser = IdCollector()
        parser.feed(self.html)
        looked_up = set(re.findall(
            r"getElementById\(['\"]([^'\"]+)", self.javascript,
        ))
        self.assertEqual(looked_up - parser.ids, set())

    def test_runtime_preserves_geometry_and_freezes_a_preloaded_block(self):
        self.assertIn("Expanded — preserve 178:64", self.html)
        self.assertIn("Compact native 712×256", self.html)
        self.assertIn("Preloading every image and audio buffer", self.javascript)
        self.assertIn("Settings changed. Generate and preload", self.javascript)
        self.assertIn("manifest.settings.split", self.javascript)
        self.assertIn("manifest.settings.signalMode", self.javascript)
        self.assertIn("manifest.settings.baseStimulusCount", self.javascript)
        self.assertIn("manifest.settings.glyphComposition", self.javascript)
        self.assertIn("manifest.settings.progression", self.javascript)
        self.assertIn("manifest.settings.feedbackEnabled", self.javascript)
        self.assertIn("manifest.settings.seed", self.javascript)
        self.assertIn("Press any key to start", self.javascript)
        self.assertIn("trial_start_method", self.javascript)
        self.assertIn("css_px_per_audio_column", self.javascript)
        self.assertIn("coordinate_mapping", self.javascript)

    def test_results_include_glyph_level_condition_analysis(self):
        self.assertIn("summarizeTrialRows", self.javascript)
        self.assertIn("Exact plates", self.javascript)
        self.assertIn("All glyphs", self.javascript)
        self.assertIn("Non-aligned transformed", self.javascript)
        self.assertIn("Non-aligned unchanged", self.javascript)
        self.assertIn("Not applicable", self.javascript)
        self.assertIn("Complete target visible", self.javascript)
        self.assertIn("Error depth", self.javascript)
        self.assertIn("Median correct RT", self.javascript)
        self.assertIn("summary-table-wrap", self.html)

    def test_full_simulator_controls_and_signal_modes_are_explicit(self):
        self.assertIn("Training set — 2/3 mappings (13/19 source families)", self.html)
        self.assertIn("Held-out test — 1/3 mappings (6/19 source families)", self.html)
        self.assertIn(
            '<option value="mixed" selected>Mixed visual vs IR — carrier-controlled</option>',
            self.html,
        )
        self.assertIn('<option value="visual">Visual baseline — silent</option>', self.html)
        self.assertIn(
            '<option value="ir">IR only — audio diagnostic</option>',
            self.html,
        )
        self.assertIn(
            '<option value="paired">Repeated pair — same puzzle twice (research)</option>',
            self.html,
        )
        self.assertIn(
            '<option value="mixed-aligned">Four-way mixed — visual and IR alignment</option>',
            self.html,
        )
        self.assertIn(
            '<option value="visual-aligned">Visual composites — complementary vs aligned (silent)</option>',
            self.html,
        )
        self.assertLess(
            self.html.index('<option value="mixed" selected>'),
            self.html.index('<option value="visual">'),
        )
        self.assertIn('<optgroup label="Advanced research mode">', self.html)
        self.assertIn('id="base-stimulus-count"', self.html)
        self.assertIn('id="base-stimulus-count" type="number" min="4" max="96" step="1" value="30"', self.html)
        self.assertIn(
            '<option value="mixed">Shuffled — balanced difficulty range</option>',
            self.html,
        )
        self.assertIn(
            '<option value="glyph-growing" selected>Glyph staircase — 1→2→3, conditions shuffled</option>',
            self.html,
        )
        self.assertIn(
            '<option value="growing">Growing practice — simpler to harder</option>',
            self.html,
        )
        self.assertIn('<option value="on" selected>On</option>', self.html)
        self.assertIn('id="feedback-enabled"', self.html)
        self.assertIn('id="response-device"', self.html)
        self.assertIn('id="presentation"', self.html)
        self.assertIn("'visual_silent': 'Visual diagnostic (silent)'", self.javascript)
        self.assertIn(
            "'visual_aligned_silent': 'Visual aligned + composite (silent)'",
            self.javascript,
        )
        self.assertIn(
            "'visual_complementary_silent': 'Complementary visual composite (silent)'",
            self.javascript,
        )
        self.assertIn(
            "'visual_background_audio': 'Visual diagnostic + neutral carrier audio'",
            self.javascript,
        )
        self.assertIn(
            "'ir_audio': 'Source scaffold + IR diagnostic audio'",
            self.javascript,
        )
        self.assertIn("mixed: 'mixed visual vs IR · carrier-controlled'", self.javascript)
        self.assertIn("Which complete glyph did this stimulus specify?", self.html)
        self.assertNotIn("complete multimodal stimulus", self.html)
        self.assertIn("visual + neutral carrier", self.javascript)
        self.assertIn("manifest.sweep_repetitions", self.javascript)
        self.assertIn("manifest.inter_sweep_interval_ms", self.javascript)
        self.assertIn("'visual_aligned_overlay'", self.javascript)
        self.assertIn("'visual_aligned_ir_audio'", self.javascript)
        self.assertIn("Visual aligned + composite · neutral carrier", self.javascript)
        self.assertIn("Complete identity visual + shifted identical IR", self.javascript)
        self.assertIn(
            "Four-way mixed includes that same yellow missing-stroke composite",
            self.html,
        )

    def test_preview_advanced_controls_and_manual_feedback_are_wired(self):
        self.assertIn("Automatic — balance 1, 2, and 3 glyphs", self.html)
        self.assertIn('<option value="1">Only 1 glyph</option>', self.html)
        self.assertIn('<option value="2">Only 2 glyphs</option>', self.html)
        self.assertIn('<option value="3">Only 3 glyphs</option>', self.html)
        self.assertIn("Reproducible run code", self.html)
        self.assertIn('id="mixed-condition-ratio"', self.html)
        self.assertIn("RGB source + yellow missing strokes", self.html)
        self.assertIn('value="1:1:1:2"', self.html)
        self.assertIn("parseMixedConditionRatio", self.javascript)
        for preview_id in (
            "preview-stimuli", "preview-presentations", "preview-glyphs",
            "preview-conditions", "preview-duration", "preview-source",
            "preview-feedback", "preview-run-code",
        ):
            self.assertIn(f'id="{preview_id}"', self.html)
        self.assertIn("(seed % 3 + index) % 3", self.javascript)
        self.assertIn("assumes a 2-second response time", self.html)
        self.assertIn("Feedback exposes held-out mappings", self.javascript)
        self.assertIn("feedback can reveal an answer", self.javascript)
        self.assertIn("signalMode === 'paired' && feedbackEnabled", self.javascript)
        self.assertIn("const extraIsVisual = seed % 2 === 0", self.javascript)
        self.assertIn("seeded extra:", self.javascript)
        self.assertIn("signalMode === 'paired' ? 2 : 1", self.javascript)
        self.assertIn("if (session.feedbackEnabled)", self.javascript)
        self.assertNotIn("if (session.split === 'train')", self.javascript)
        for request_key in (
            "signalMode", "baseStimulusCount", "glyphComposition",
            "progression", "feedbackEnabled",
        ):
            self.assertRegex(self.javascript, rf"\n\s+{request_key}:")

    def test_participant_preferences_and_randomization_audit_are_mandatory(self):
        self.assertIn('id="results-directory"', self.html)
        self.assertIn('id="participant-picker"', self.html)
        self.assertIn('id="register-participant-btn"', self.html)
        self.assertIn('id="remember-preferences-btn"', self.html)
        self.assertIn('id="exposure-status"', self.html)
        self.assertIn('id="release-abandoned-btn"', self.html)
        self.assertIn('id="release-abandoned-status"', self.html)
        self.assertIn('id="randomization-audit"', self.html)
        self.assertIn('id="audit-eligible"', self.html)
        self.assertIn('id="audit-history"', self.html)
        self.assertIn('id="audit-historical-repeats"', self.html)
        self.assertIn('id="audit-within-candidate-repeats"', self.html)
        self.assertIn('id="audit-repeats"', self.html)
        self.assertIn('id="audit-threshold"', self.html)
        self.assertIn('id="audit-requested-seed"', self.html)
        self.assertIn('id="audit-effective-seed"', self.html)
        self.assertIn('id="audit-rerandomizations"', self.html)
        self.assertIn("fetch('/api/local-state'", self.javascript)
        self.assertIn("fetch('/api/preferences'", self.javascript)
        self.assertIn("fetch('/api/participants'", self.javascript)
        self.assertIn("fetch('/api/record-exposure'", self.javascript)
        self.assertIn("fetch('/api/revalidate-session'", self.javascript)
        self.assertIn("fetch('/api/release-session'", self.javascript)
        self.assertIn("fetch('/api/force-release-session'", self.javascript)
        self.assertIn("participantId,", self.javascript)
        self.assertIn("resultsDirectory: resultsDirectoryInput.value.trim()", self.javascript)
        self.assertIn("Math.floor(threshold * baseStimulusSlots)", self.javascript)
        self.assertIn("new Set(generatedManifest.stimuli.map", self.javascript)
        self.assertIn("updatePreparedPreview(preparedManifest, preparedAudit)", self.javascript)
        self.assertIn("randomizationAudit.accepted", self.javascript)
        self.assertIn("randomizationAudit", self.javascript)
        self.assertIn("historical_repeat_rate", self.javascript)
        self.assertIn("randomization_threshold", self.javascript)
        self.assertIn("candidateSignatureDigest", self.javascript)
        self.assertIn("eligibleByGlyph", self.javascript)
        self.assertIn("eligible glyph-count breakdown", self.javascript)
        self.assertIn("total · ${eligibleGlyphBreakdown}", self.javascript)
        self.assertIn("preparationGeneration", self.javascript)
        self.assertIn("readPreparationSnapshot", self.javascript)
        self.assertIn("exposureStatus", self.javascript)

    def test_start_and_end_transitions_are_race_guarded(self):
        self.assertIn("let revalidationSucceeded = false", self.javascript)
        self.assertIn("phase = 'starting'", self.javascript)
        self.assertIn("preparationGeneration !== revalidationGeneration", self.javascript)
        self.assertIn("session !== startingSession", self.javascript)
        self.assertIn("phase = 'finishing'", self.javascript)
        self.assertIn("phase = 'saving'", self.javascript)
        self.assertIn("const saveSession = session", self.javascript)
        self.assertIn("const rowsToSave = [...trialRows]", self.javascript)
        self.assertIn("newButton.disabled = true", self.javascript)
        self.assertIn(
            "phase !== 'finished' || !endStateReadyForReset || activeSessionLease",
            self.javascript,
        )
        self.assertIn("normalizeSessionLease", self.javascript)
        self.assertIn("finalizeExposureHistory", self.javascript)
        self.assertIn("if (!failedExposureCount && activeSessionLease)", self.javascript)
        self.assertIn("let resultsSaved = false", self.javascript)
        self.assertIn("if (!resultsSaved)", self.javascript)
        self.assertIn("savedResultInfo = await saveCsv", self.javascript)
        self.assertIn("savedResultInfo?.downloaded !== true", self.javascript)
        self.assertIn("endStateReadyForReset = readyForNewBlock && resultsSaved", self.javascript)
        self.assertIn("newButton.disabled = !endStateReadyForReset", self.javascript)
        self.assertIn("phase !== 'finished' || !endStateReadyForReset", self.javascript)
        self.assertIn("info.code === 'participant_session_active'", self.javascript)
        self.assertIn("confirmAbandonedSession: true", self.javascript)
        self.assertIn("expectedSessionId: conflict.activeSessionId", self.javascript)
        self.assertIn("expectedPreparationId: conflict.activePreparationId", self.javascript)
        self.assertIn("'active_session_changed'", self.javascript)
        self.assertIn("Object.freeze({", self.javascript)
        self.assertIn("window.confirm(", self.javascript)
        self.assertIn("hideAbandonedSessionRecoveryIfParticipantChanged", self.javascript)
        self.assertIn("window.addEventListener('pagehide'", self.javascript)
        self.assertIn("event.persisted", self.javascript)
        self.assertIn("pendingExposureRequests.size", self.javascript)
        self.assertIn("failedExposurePayloads.size", self.javascript)
        self.assertIn("navigator.sendBeacon(", self.javascript)

    def test_response_order_device_and_analysis_metadata_are_preserved(self):
        self.assertIn("trial.response_choice_ids", self.javascript)
        self.assertIn("displayed_choice_order", self.javascript)
        self.assertIn("session?.responseDevice === 'keyboard'", self.javascript)
        self.assertIn("session?.responseDevice === 'pointer'", self.javascript)
        self.assertIn("responseInputMethod !== session?.responseDevice", self.javascript)
        for field in (
            "pair_order", "pair_pass", "pair_lag", "transformation_signature",
            "mapping_repetition_index", "estimated_difficulty_score",
            "difficulty_rank", "difficulty_stratum",
            "difficulty_glyph_load", "difficulty_diagnostic_subtlety",
            "difficulty_alternative_foil_similarity", "difficulty_family_ambiguity",
            "displayed_choice_targets_json", "target_choice_id", "decoy_choice_id",
            "schema_version", "glyph_quota_1", "total_presentation_count",
            "comparison_design", "stimuli_repeated_across_conditions",
            "condition_count_visual_complementary_silent",
            "condition_count_visual_aligned_silent",
            "condition_count_visual_background_audio", "condition_assignment_method",
            "difficulty_match_id", "difficulty_match_position",
            "difficulty_match_score_gap", "assigned_condition",
            "mapping_class", "choice_rule", "combinatorial_verification_passed",
            "combinatorial_eligible_by_glyph_json",
            "condition_by_glyph_count_json",
            "canonical_target_mask_sha256", "aligned_target_mask_sha256",
            "aligned_visual_base_mask_sha256",
            "aligned_visual_shifted_mask_sha256",
            "aligned_visual_palette_version",
            "visible_base_colours_json",
            "aligned_visual_base_colours_json",
            "aligned_visual_copy_colour_json",
            "visual_complementary_equivalence_version",
            "visual_complementary_addition_colour_json",
            "visual_complementary_source_dot_count",
            "visual_complementary_addition_dot_count",
            "visual_complementary_carrier_occupancy_sha256",
            "visual_complementary_source_mask_sha256",
            "visual_complementary_addition_mask_sha256",
            "visual_complementary_target_mask_sha256",
            "aligned_visual_carrier_version",
            "aligned_visual_density_equivalence_version",
            "aligned_visual_pair_axis",
            "aligned_visual_dot_pitch_pixels",
            "aligned_visual_pair_offset_pixels",
            "aligned_visual_subdot_radii_json",
            "aligned_visual_carrier_dot_count",
            "aligned_visual_subdot_count",
            "aligned_visual_carrier_radius_histogram_json",
            "aligned_visual_carrier_occupied_pixel_count",
            "visible_signal_dot_count",
            "balanced_visual_source_dot_count",
            "balanced_visual_source_radius_histogram_json",
            "balanced_visual_source_radius_area_units",
            "balanced_visual_source_active_pixel_count",
            "aligned_visual_base_channel_position",
            "aligned_visual_shifted_channel_position",
            "aligned_visual_base_radius_histogram_json",
            "aligned_visual_shifted_radius_histogram_json",
            "aligned_visual_base_radius_area_units",
            "aligned_visual_shifted_radius_area_units",
            "aligned_visual_base_active_pixel_count",
            "aligned_visual_shifted_active_pixel_count",
            "balanced_carrier_occupancy_sha256",
            "canonical_carrier_occupancy_sha256",
            "aligned_carrier_occupancy_sha256",
        ):
            self.assertIn(field, self.javascript)

    def test_launcher_packages_only_the_advanced_runtime(self):
        template = ROOT / "tools" / "advanced_app_template"
        executable = template / "Advanced-Ishihara-Launcher"
        metadata = plistlib.loads((template / "Info.plist").read_bytes())
        launcher = executable.read_text()
        packager = (ROOT / "tools" / "package_advanced_app.sh").read_text()
        server = (ROOT / "shared" / "experiment_server.py").read_text()
        self.assertTrue(os.access(executable, os.X_OK))
        self.assertEqual(metadata["CFBundleExecutable"], executable.name)
        self.assertIn('server_port="__SERVER_PORT__"', launcher)
        self.assertIn('runtime_id="__RUNTIME_ID__"', launcher)
        self.assertIn('/api/runtime-identity', launcher)
        self.assertIn('ADVANCED_ISHIHARA_RUNTIME_ID', launcher)
        self.assertIn('/advanced/', launcher)
        self.assertIn('Application Support/__DATA_NAME__', launcher)
        self.assertIn('ADVANCED_ISHIHARA_MIRROR_DATA_DIR', launcher)
        self.assertIn('ADVANCED_ISHIHARA_APP_NAME', packager)
        self.assertIn('ADVANCED_ISHIHARA_PORT', packager)
        self.assertIn('runtime_source_digest', packager)
        self.assertIn('Dev/Lossfunk/ir-results/ishihara-alignment-lab', packager)
        self.assertIn('PYTHONDONTWRITEBYTECODE=1', launcher)
        self.assertNotIn(str(ROOT), launcher)
        self.assertIn('advanced_ishihara/grammar_snapshot.json', launcher)
        self.assertIn('export_advanced_catalog.mjs', packager)
        self.assertIn('codesign --verify', packager)
        self.assertNotIn('ishihara_stimuli', packager)
        self.assertIn('/api/prepare-session', server)
        self.assertIn('/api/runtime-identity', server)
        self.assertIn('/api/save-run', server)
        self.assertIn('prefix = "/advanced_sessions/"', server)
        self.assertIn('candidate.relative_to(session_root)', server)


if __name__ == "__main__":
    unittest.main()
