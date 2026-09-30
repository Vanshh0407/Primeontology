from django.core.management.base import BaseCommand

from prime_ontology.mcp_server import serve


class Command(BaseCommand):
    help = "Run the read-only Prime Ontology MCP server over stdio."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", default="", help="Restrict to one tenant's ontologies.")

    def handle(self, *args, **opts):
        serve(opts["tenant"])
