"""Continuous ingestion: `manage.py fabric_worker` polls due sources forever; `--once` runs one pass (cron / tests)."""
import time

from django.core.management.base import BaseCommand

from prime_ontology.fabric.http import ConnectorError
from prime_ontology.fabric.sync import SyncBusy, due_sources, sync_source


class Command(BaseCommand):
    help = "Synchronise fabric sources whose schedule is due."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll", type=int, default=30, help="seconds between passes")

    def handle(self, *a, once=False, poll=30, **kw):
        while True:
            for s in list(due_sources()):
                try:
                    r = sync_source(s, trigger="schedule", actor="fabric_worker")
                    self.stdout.write(f"{s.ontology_id}/{s.name}: {r.status} {r.stats}")
                except (SyncBusy, ConnectorError) as e:
                    self.stdout.write(f"{s.ontology_id}/{s.name}: skipped ({e})")
            if once:
                return
            time.sleep(max(1, poll))
