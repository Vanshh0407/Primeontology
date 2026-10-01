"""Run the backend test-suite against several MySQL/MariaDB server versions in throw-away Docker containers.

    backend/.venv/Scripts/python scripts/test_mysql_versions.py [image ...]
Default images: mysql:5.7 (as a source only) mysql:8.4 mariadb:10.11 mariadb:11

For each image: start a container, load demo/demo_schema.sql into `primeontology_demo`, then run `manage.py test` with
  * the app's OWN storage on that server (PRIME_DB_ENGINE=mysql -> migrations, JSON columns, branching, concurrency ...)
  * the live-source tests (introspection, sample records) pointed at the same server.
Containers are always removed. The password is generated per run and never written to disk.
"""
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = BACKEND / ".venv" / "bin" / "python"
IMAGES = sys.argv[1:] or ["mysql:5.7", "mysql:8.4", "mariadb:10.11", "mariadb:11"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def wait_ready(port, password, seconds=240):
    import pymysql

    end = time.time() + seconds
    last = None
    while time.time() < end:
        try:
            c = pymysql.connect(host="127.0.0.1", port=port, user="root", password=password, connect_timeout=3)
            c.close()
            return True
        except Exception as e:  # server still initialising
            last = e
            time.sleep(3)
    print("   never became ready:", last)
    return False


def load_demo(port, password):
    import pymysql

    c = pymysql.connect(host="127.0.0.1", port=port, user="root", password=password, autocommit=True)
    cur = c.cursor()
    cur.execute("CREATE DATABASE IF NOT EXISTS primeontology_demo CHARACTER SET utf8mb4")
    cur.execute("USE primeontology_demo")
    for st in [x.strip() for x in (ROOT / "demo" / "demo_schema.sql").read_text(encoding="utf-8").split(";") if x.strip()]:
        cur.execute(st)
    cur.execute("SELECT VERSION()")
    v = cur.fetchone()[0]
    c.close()
    return v


def main():
    import pymysql  # noqa: F401  (fail early if the venv is wrong)

    summary = []
    for image in IMAGES:
        name = "pontest-" + re.sub(r"[^a-z0-9]", "", image)
        port, pw = free_port(), secrets.token_urlsafe(12)
        print(f"\n=== {image} (port {port})")
        run(["docker", "rm", "-f", name])
        r = run(["docker", "run", "-d", "--name", name, "-e", f"MYSQL_ROOT_PASSWORD={pw}", "-e", f"MARIADB_ROOT_PASSWORD={pw}",
                "-p", f"127.0.0.1:{port}:3306", image])
        if r.returncode:
            print("   cannot start:", r.stderr.strip()[:200])
            summary.append((image, "could not start", ""))
            continue
        try:
            if not wait_ready(port, pw):
                summary.append((image, "did not become ready", ""))
                continue
            version = load_demo(port, pw)
            env = {**os.environ, "PRIME_DB_ENGINE": "mysql", "PRIME_DB_HOST": "127.0.0.1", "PRIME_DB_PORT": str(port), "PRIME_DB_USER": "root",
                   "PRIME_DB_PASSWORD": pw, "PRIME_DB_NAME": "primeontology", "PRIME_TEST_MYSQL_PASSWORD": pw, "PRIME_TEST_MYSQL_HOST": "127.0.0.1",
                   "PRIME_TEST_MYSQL_PORT": str(port), "PRIME_TEST_MYSQL_DB": "primeontology_demo", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
            import pymysql

            c = pymysql.connect(host="127.0.0.1", port=port, user="root", password=pw, autocommit=True)
            c.cursor().execute("CREATE DATABASE IF NOT EXISTS primeontology CHARACTER SET utf8mb4")
            c.close()
            t = time.time()
            cmd = [str(PY), "manage.py", "test"]
            if re.match(r"mysql:[0-7]\.", image):
                # Django 5 refuses MySQL < 8.0.11 as ITS OWN database, but such servers can still be *sources* to introspect:
                # test exactly that (live-source tests) and keep the app's storage on SQLite for this run only.
                env["PRIME_DB_ENGINE"] = "sqlite"
                cmd += ["prime_ontology.tests.test_live_mysql"]
                print("   (storage requires MySQL >= 8.0.11; testing this server as a SOURCE only)")
            res = subprocess.run(cmd, cwd=BACKEND, env=env, capture_output=True, text=True)
            tail = "\n".join((res.stdout + res.stderr).strip().splitlines()[-6:])
            ok = res.returncode == 0
            ran = re.search(r"Ran (\d+) tests?", res.stdout + res.stderr)
            skipped = re.search(r"skipped=(\d+)", res.stdout + res.stderr)
            print(f"   server {version}: {'PASS' if ok else 'FAIL'}  ({ran.group(1) if ran else '?'} tests, skipped={skipped.group(1) if skipped else 0}, {time.time() - t:.0f}s)")
            if not ok:
                print("   " + tail.replace("\n", "\n   "))
            summary.append((image, "PASS" if ok else "FAIL", f"{version}; {ran.group(1) if ran else '?'} tests"))
        finally:
            run(["docker", "rm", "-f", name])
    print("\n=== summary")
    for image, status, detail in summary:
        print(f"  {image:16} {status:6} {detail}")
    return 0 if all(s == "PASS" for _, s, _ in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
