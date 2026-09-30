#!/usr/bin/env python
"""Install the Prime Ontology module into a host application (UniContractAI / PrimeSemOnto / PrimeAgentic OS).

  python scripts/integrate.py --host unicontractai --target C:/src/unicontractai \
      [--frontend-dir frontend] [--backend-dir backend] [--dry-run]

What it does (idempotent; never overwrites host files that already exist unless --force):
  backend : copies backend/prime_ontology/ -> <target>/<backend-dir>/prime_ontology/ and the host settings file
  frontend: packs @prime/ontology-workbench into <target>/<frontend-dir>/vendor/, adds it (+ config, page, next.config snippet)
Then prints the remaining manual steps (INSTALLED_APPS, urls.py, migrate, npm install).
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOSTS = ("unicontractai", "primesemonto", "primeagenticos")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "tests", ".venv", "db.sqlite3")


def copy_file(src: Path, dst: Path, force: bool, dry: bool, log: list):
    if dst.exists() and not force:
        log.append(f"  skip   {dst} (exists; use --force to overwrite)")
        return
    log.append(f"  {'would write' if dry else 'write'} {dst}")
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, choices=HOSTS)
    ap.add_argument("--target", required=True, help="Path to the host repository root")
    ap.add_argument("--frontend-dir", default="frontend")
    ap.add_argument("--backend-dir", default="backend")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    target = Path(a.target).resolve()
    if not target.is_dir():
        print(f"error: target {target} is not a directory", file=sys.stderr)
        return 2
    fe, be = target / a.frontend_dir, target / a.backend_dir
    adapter = ROOT / "adapters" / a.host
    log: list[str] = []

    # backend: the Django app (tests excluded)
    src_app = ROOT / "backend" / "prime_ontology"
    for f in src_app.rglob("*"):
        if f.is_file() and not any(p in f.parts for p in ("__pycache__", "tests")) and f.suffix != ".pyc":
            copy_file(f, be / "prime_ontology" / f.relative_to(src_app), a.force, a.dry_run, log)
    copy_file(adapter / "django" / "prime_ontology_settings.py", be / "prime_ontology_settings.py", a.force, a.dry_run, log)

    # frontend: the reusable module (source), adapter files
    src_ui = ROOT / "frontend" / "src" / "workbench"
    for f in src_ui.rglob("*"):
        if f.is_file() and not f.name.endswith(".test.js"):
            copy_file(f, fe / "vendor" / "prime-ontology" / "src" / "workbench" / f.relative_to(src_ui), a.force, a.dry_run, log)
    copy_file(ROOT / "frontend" / "package.json", fe / "vendor" / "prime-ontology" / "package.json", a.force, a.dry_run, log)
    app_root = fe / "src" if (fe / "src").is_dir() else fe  # page imports ../../prime-ontology.config relative to app/ontology
    copy_file(adapter / "app/ontology/page.jsx", app_root / "app/ontology/page.jsx", a.force, a.dry_run, log)
    copy_file(adapter / "prime-ontology.config.js", app_root / "prime-ontology.config.js", a.force, a.dry_run, log)
    copy_file(adapter / "next.config.snippet.js", fe / "next.config.prime-ontology.snippet.js", a.force, a.dry_run, log)

    print("\n".join(log))
    print(f"""
Manual steps for {a.host}:
  backend  1. add "prime_ontology" to INSTALLED_APPS and `from .prime_ontology_settings import *` (or paste its values)
           2. urls.py: path("api/v1/ontology/", include("prime_ontology.urls"))
           3. pip install -r <prime-ontology>/backend/requirements.txt ; python manage.py migrate
  frontend 4. npm install ./vendor/prime-ontology   (adds @prime/ontology-workbench, @xyflow/react, @dagrejs/dagre)
           5. merge next.config.prime-ontology.snippet.js into next.config.js (transpilePackages + API rewrite)
           6. open /ontology in the running host (page: {'src/' if (fe / 'src').is_dir() else ''}app/ontology/page.jsx)
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
