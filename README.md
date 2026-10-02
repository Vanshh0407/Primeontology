# Prime Ontology Platform

One reusable ontology engine + workbench, embedded unchanged in **UniContractAI**, **PrimeSemOnto** and **PrimeAgentic OS**
(Next.js/React/Material UI + Django/Python). Implements the 10-release plan in `PrimeOntology.txt`.

| Ports | |
|---|---|
| Backend (Django API) | **8008** |
| Frontend (React workbench) | **3008** |

Database: **MySQL only** (as source *and* for the app's own storage; SQLite is used only when `PRIME_DB_ENGINE` is unset).

## Login
The standalone app requires a login (Django session). **Demo login:** `admin` / `admin123` — click **Demo login** on the login page to fill them in, then **Sign in**.
Create/reset it with `python manage.py seed_demo_users` (Docker does this when `PRIME_SEED_DEMO_USER=1`). `--with-viewer` adds a read-only `viewer` / `viewer123`.
These are public demo credentials: never seed them on a shared or production system. Real users: `manage.py createsuperuser` (admin) or add users to the groups *Ontology Viewers / Editors / Reviewers / Admins*.

## Run it

**Docker (self-contained, incl. its own MySQL + demo database):**
```bash
cp .env.example .env        # set PRIME_DB_PASSWORD
docker compose up --build   # http://localhost:3008
```
In the UI: *Import & Sources → MySQL* → host `mysql`, user `root`, your password, database `primeontology_demo` → **Generate ontology**.

**Local dev (your own MySQL):**
```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt      # Windows; use .venv/bin on Linux/macOS
# create an empty database for the app once:  CREATE DATABASE primeontology CHARACTER SET utf8mb4;
export PRIME_DB_ENGINE=mysql PRIME_DB_PASSWORD=...   # PRIME_DB_HOST/PORT/USER/NAME optional (root@localhost:3306/primeontology)
python manage.py migrate && python manage.py runserver 8008
cd ../frontend && npm install && npm run dev                              # http://localhost:3008 (proxies /api -> :8008)
```
Windows shortcut: put `PRIME_DB_ENGINE` / `PRIME_DB_PASSWORD` (and optionally `PRIME_WORKFLOW_BASE_URL`) in `.env`, then run
`powershell -ExecutionPolicy Bypass -File .\start-dev.ps1` — it opens the backend and frontend in their own windows.

**n8n:** create a token under *Enterprise OS → API tokens*, export a plan from *Autonomy → Export n8n*, import it into n8n and replace
`PASTE_PRIME_SERVICE_TOKEN` in each HTTP node. When n8n runs in Docker, set `PRIME_WORKFLOW_BASE_URL=http://host.docker.internal:8008`
so exported nodes can reach the backend.

Demo source database: `demo/demo_schema.sql` (MySQL dialect) → create a schema such as `primeontology_demo` and run it.
Sample files for every importer: `demo/samples/`.
Standalone URL options: `?context=unicontractai|primesemonto|primeagenticos&ontology=<id>&tab=<tab>` (roles come from the logged-in user).

## Optional capabilities
`pip install -r backend/requirements-optional.txt` adds **OCR for scanned PDFs** (pypdfium2 + RapidOCR) and the **semantic embedding model** (fastembed; ~130 MB downloaded once, in the background). Both are local — no cloud calls. The app runs without them and the UI says what is active. `ANTHROPIC_API_KEY` enables the LLM assistant/extraction; REST/JSON sources need `PRIME_ONTOLOGY_ALLOWED_URL_HOSTS`. `python scripts/make_samples.py` regenerates the binary demo samples (`shop.db`, `customers.parquet`, `scanned_agreement.pdf`).

## Layout
```
backend/prime_ontology/   Django app: ingest/, generator, validation, reasoning, query, mapping, versioning, agent, mcp_server, identity, views
backend/config/           standalone host settings (ports, MySQL, sessions)
frontend/src/workbench/   @prime/ontology-workbench (the ONE React module) + api.js SDK
frontend/e2e/             browser end-to-end tests (Chrome via playwright-core)
adapters/<host>/          per-host files (page.jsx identical in all three; only config differs)
scripts/integrate.py      installs the module into a host repo
docker/, docker-compose.yml, demo/, docs/
```

## Tests
```bash
cd backend && PRIME_DB_ENGINE=mysql PRIME_DB_PASSWORD=... .venv/Scripts/python manage.py test   # 127 tests (creates/drops test_<db>; 3 live-MySQL tests also need PRIME_TEST_MYSQL_PASSWORD)
cd frontend && npm test                                                                            # 13 unit tests
python scripts/test_adapters.py                                                                    # 4 tests: same-code guarantee + installer
cd frontend && MYSQL_TEST_PASSWORD=... npm run e2e                                                 # 16 browser steps incl. login (needs both servers running and `manage.py seed_demo_users --with-viewer`)
cd frontend && npm run e2e:features                                                                # 14 steps: records, individuals, groups, branches, concurrency, OCR, Parquet, scale
cd frontend && node e2e/perf.mjs 300 1000 2000                                                     # large-ontology timings
backend/.venv/Scripts/python scripts/test_mysql_versions.py                                        # full suite on MySQL 8.4 / MariaDB 10.11 / 11 (+5.7 as a source) in Docker
```

## Docs
[docs/RELEASES.md](docs/RELEASES.md) what is built vs the plan (honest status) · [docs/INTEGRATION.md](docs/INTEGRATION.md) embedding into the 3 products ·
[docs/API.md](docs/API.md) · [docs/ORIONBELT_AUDIT.md](docs/ORIONBELT_AUDIT.md) **(licence finding — read this)** · [docs/SECURITY.md](docs/SECURITY.md)
