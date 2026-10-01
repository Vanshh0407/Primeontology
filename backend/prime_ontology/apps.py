import os
import sys

from django.apps import AppConfig


class PrimeOntologyConfig(AppConfig):
    name = "prime_ontology"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from .rag import ingest as rag_ingest

        from .twin import core as twin_core

        twin_core.register_hooks()  # temporal history + event stream follow golden-record changes
        rag_ingest.register_hooks()  # keep document/entity links in step with fabric syncs
        # Load the embedding model in the background when a server process starts (not for migrate/test/shell), so the first
        # user request never waits for it. Without it, matching/search fall back to the lexical engine.
        argv = " ".join(sys.argv)
        serving = any(k in argv for k in ("runserver", "gunicorn", "uvicorn", "daphne", "waitress"))
        if serving and os.environ.get("RUN_MAIN") != "false" and os.environ.get("PRIME_ONTOLOGY_EMBEDDINGS", "auto").lower() in ("auto", "fastembed"):
            from . import embeddings

            embeddings.warm_up()
