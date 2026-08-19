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
        self.assertIn("manifest.settings.mode", self.javascript)
        self.assertIn("manifest.settings.seed", self.javascript)
        self.assertIn("Press any key to start", self.javascript)
        self.assertIn("trial_start_method", self.javascript)
        self.assertIn("css_px_per_audio_column", self.javascript)
        self.assertIn("coordinate_mapping", self.javascript)

    def test_two_modes_make_the_audio_comparison_explicit(self):
        self.assertIn('<option value="visual-only">Visual-only baseline — silent</option>', self.html)
        self.assertIn('<option value="mixed">Paired visible versus IR</option>', self.html)
        self.assertIn("'visual-only': 'Visual-only baseline'", self.javascript)
        self.assertIn("'visible-composite': 'Visible probe + background carrier'", self.javascript)
        self.assertIn("'ir-composite': 'IR probe'", self.javascript)
        self.assertIn("manifest.sweep_repetitions", self.javascript)
        self.assertIn("manifest.inter_sweep_interval_ms", self.javascript)

    def test_launcher_packages_only_the_advanced_runtime(self):
        template = ROOT / "tools" / "advanced_app_template"
        executable = template / "Advanced-Ishihara-Launcher"
        metadata = plistlib.loads((template / "Info.plist").read_bytes())
        launcher = executable.read_text()
        packager = (ROOT / "tools" / "package_advanced_app.sh").read_text()
        server = (ROOT / "shared" / "experiment_server.py").read_text()
        self.assertTrue(os.access(executable, os.X_OK))
        self.assertEqual(metadata["CFBundleExecutable"], executable.name)
        self.assertIn('server_port="8137"', launcher)
        self.assertIn('/advanced/', launcher)
        self.assertIn('Application Support/Advanced IR Ishihara', launcher)
        self.assertNotIn(str(ROOT), launcher)
        self.assertIn('advanced_ishihara/grammar_snapshot.json', launcher)
        self.assertIn('export_advanced_catalog.mjs', packager)
        self.assertIn('codesign --verify', packager)
        self.assertNotIn('ishihara_stimuli', packager)
        self.assertIn('/api/prepare-session', server)
        self.assertIn('/api/save-run', server)
        self.assertIn('prefix = "/advanced_sessions/"', server)
        self.assertIn('candidate.relative_to(session_root)', server)


if __name__ == "__main__":
    unittest.main()
