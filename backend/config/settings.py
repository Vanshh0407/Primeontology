"""Standalone host settings. In UniContractAI / PrimeSemOnto / PrimeAgentic OS,
just add "prime_ontology" to INSTALLED_APPS and include its urls."""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "prime_ontology",
]

MIDDLEWARE = [
    "prime_ontology.middleware.SimpleCorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "config.urls"

# Persistence: SQLite by default; set PRIME_DB_ENGINE=mysql + PRIME_DB_* to store ontologies in MySQL.
_engine = os.environ.get("PRIME_DB_ENGINE", "sqlite")
if _engine == "mysql":
    import pymysql

    pymysql.version_info = (2, 2, 1, "final", 0)  # satisfy Django's driver version check
    pymysql.install_as_MySQLdb()
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.mysql", "NAME": os.environ.get("PRIME_DB_NAME", "primeontology"),
        "USER": os.environ.get("PRIME_DB_USER", "root"), "PASSWORD": os.environ.get("PRIME_DB_PASSWORD", ""),
        "HOST": os.environ.get("PRIME_DB_HOST", "localhost"), "PORT": os.environ.get("PRIME_DB_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4"}}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

# Comma-separated origins allowed to call the API cross-origin (dev only).
PRIME_ONTOLOGY_CORS_ORIGINS = os.environ.get(
    "PRIME_ONTOLOGY_CORS_ORIGINS", "http://localhost:3008,http://127.0.0.1:3008"
).split(",")

# Identity: hosts set PRIME_ONTOLOGY_IDENTITY (see prime_ontology/identity.py). Without it the standalone header identity
# is used and, outside DEBUG, defaults to read-only. The automated test-suite runs as admin.
if "test" in sys.argv:
    PRIME_ONTOLOGY_DEFAULT_ROLE = "admin"
    PRIME_ONTOLOGY_EMBEDDINGS = "hash"  # deterministic + offline; the model-backed tests opt in explicitly
elif os.environ.get("PRIME_STANDALONE_AUTH", "1") == "1":
    # The standalone app requires a login (Django session). Roles: superuser -> admin; group map below; else viewer.
    PRIME_ONTOLOGY_IDENTITY = "prime_ontology.identity.django_user_identity"
    PRIME_ONTOLOGY_AUTHENTICATED_ROLE = "viewer"
    PRIME_ONTOLOGY_ROLE_MAP = {"Ontology Viewers": "viewer", "Ontology Editors": "editor",
                               "Ontology Reviewers": "reviewer", "Ontology Admins": "admin"}
    SESSION_COOKIE_SECURE = os.environ.get("DJANGO_SECURE_COOKIES", "0") == "1"


# Data-lake roots the fabric may read (name -> absolute folder). Empty = data-lake connectors disabled.
# Example: PRIME_ONTOLOGY_DATALAKE_ROOTS=lake=/mnt/lake;archive=/mnt/archive
PRIME_ONTOLOGY_DATALAKE_ROOTS = dict(p.split("=", 1) for p in os.environ.get("PRIME_ONTOLOGY_DATALAKE_ROOTS", "").split(";") if "=" in p)
