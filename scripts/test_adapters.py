"""Guards the "same code, different host" promise. Run: python scripts/test_adapters.py"""
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOSTS = ("unicontractai", "primesemonto", "primeagenticos")
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


class AdapterTests(unittest.TestCase):
    def test_page_and_next_snippet_identical_across_hosts(self):
        for rel in ("app/ontology/page.jsx",):
            self.assertEqual(len({sha(ROOT / "adapters" / h / rel) for h in HOSTS}), 1, rel)
        # the snippet differs by nothing at all
        self.assertEqual(len({sha(ROOT / "adapters" / h / "next.config.snippet.js") for h in HOSTS}), 1)

    def test_only_config_files_differ(self):
        for name in ("prime-ontology.config.js", "django/prime_ontology_settings.py"):
            self.assertEqual(len({sha(ROOT / "adapters" / h / name) for h in HOSTS}), 3, name)
        ctx = {h: (ROOT / "adapters" / h / "prime-ontology.config.js").read_text() for h in HOSTS}
        for h in HOSTS:
            self.assertIn(f"context: '{h}'", ctx[h])

    def test_integrate_installs_and_is_idempotent(self):
        for h in HOSTS:
            with tempfile.TemporaryDirectory() as t:
                (Path(t) / "frontend" / "src").mkdir(parents=True)
                run = lambda *extra: subprocess.run([sys.executable, str(ROOT / "scripts" / "integrate.py"), "--host", h, "--target", t, *extra],
                                                    capture_output=True, text=True)
                r = run()
                self.assertEqual(r.returncode, 0, r.stderr)
                fe, be = Path(t) / "frontend", Path(t) / "backend"
                self.assertTrue((fe / "src/app/ontology/page.jsx").exists())
                self.assertTrue((fe / "src/prime-ontology.config.js").exists())  # next to the page's ../../ import
                self.assertTrue((fe / "vendor/prime-ontology/src/workbench/PrimeOntologyWorkbench.jsx").exists())
                self.assertTrue((be / "prime_ontology/views.py").exists())
                self.assertTrue((be / "prime_ontology_settings.py").exists())
                self.assertFalse((be / "prime_ontology/tests").exists())
                self.assertFalse(list((fe / "vendor").rglob("*.test.js")))
                (fe / "src/prime-ontology.config.js").write_text("// host edit")
                self.assertIn("skip", run().stdout)  # does not clobber host edits
                self.assertEqual((fe / "src/prime-ontology.config.js").read_text(), "// host edit")

    def test_dry_run_and_bad_target(self):
        with tempfile.TemporaryDirectory() as t:
            r = subprocess.run([sys.executable, str(ROOT / "scripts/integrate.py"), "--host", "primesemonto", "--target", t, "--dry-run"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(list(Path(t).iterdir()), [])
        r = subprocess.run([sys.executable, str(ROOT / "scripts/integrate.py"), "--host", "primesemonto", "--target", "/no/such/dir"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
