"""Create (or refresh) an "Enterprise Demo" ontology that exercises R11-R15: three systems, documents, twin rules, agents, policies."""
from django.core.management.base import BaseCommand

from prime_ontology.autonomy.models import Agent
from prime_ontology.fabric import crypto
from prime_ontology.fabric.models import FabricSource
from prime_ontology.fabric.sync import sync_source
from prime_ontology.models import Ontology
from prime_ontology.osplane.models import Policy
from prime_ontology.rag.ingest import ingest_document
from prime_ontology.twin.models import TwinRule

NAME = "Enterprise Demo"
MODEL = {
    "classes": [{"name": n, "label": n, "parents": []} for n in ("Customer", "Supplier", "Invoice", "Contract")],
    "dataProperties": [
        {"name": "customerName", "domain": "Customer", "datatype": "string"}, {"name": "email", "domain": "Customer", "datatype": "string"},
        {"name": "city", "domain": "Customer", "datatype": "string"}, {"name": "creditLimit", "domain": "Customer", "datatype": "decimal"},
        {"name": "supplierName", "domain": "Supplier", "datatype": "string"},
        {"name": "amount", "domain": "Invoice", "datatype": "decimal"}, {"name": "overdueDays", "domain": "Invoice", "datatype": "integer"},
        {"name": "title", "domain": "Contract", "datatype": "string"}],
    "objectProperties": [
        {"name": "hasCustomer", "domain": "Invoice", "range": "Customer", "cardinality": "many-to-one"},
        {"name": "hasSupplier", "domain": "Contract", "range": "Supplier", "cardinality": "many-to-one"},
        {"name": "hasInvoice", "domain": "Contract", "range": "Invoice", "cardinality": "many-to-many"}],
}
ID = [{"keys": ["email"], "normalize": "email"}]
SOURCES = [
    ("SAP", 90, "id,name,email,city,limit\nS100,Ann Lee,ann@acme.com,Pune,5000\nS101,Raj Patel,raj@borealis.in,Delhi,9000\n",
     {"class": "Customer", "id": "id", "fields": {"name": "customerName", "email": "email", "city": "city", "limit": "creditLimit"}, "identity": ID}),
    ("Salesforce", 60, "id,name,email,city\nSF1,Ann  Lee,ANN@acme.com,Mumbai\nSF2,Cobalt Works,hello@cobalt.io,Goa\n",
     {"class": "Customer", "id": "id", "fields": {"name": "customerName", "email": "email", "city": "city"}, "identity": ID}),
    ("Procurement", 50, "id,name\nP1,Acme Steel\nP2,Borealis Metals\nP3,Cobalt Works\n",
     {"class": "Supplier", "id": "id", "fields": {"name": "supplierName"}, "identity": []}),
    ("Billing", 50, "id,cust,amount,days\nI1,S100,60,5\nI2,S100,30,70\nI3,S101,100,0\nI4,SF2,300,90\n",
     {"class": "Invoice", "id": "id", "fields": {"amount": "amount", "days": "overdueDays"}, "identity": [],
      "relations": [{"field": "cust", "target": "Customer", "property": "hasCustomer"}]}),
    ("Legal", 50, "id,title,supplier,invoice\nC1,Steel Supply 2026,P1,I1\nC2,Metals Framework,P2,I3\nC3,Cobalt Services,P3,I4\n",
     {"class": "Contract", "id": "id", "fields": {"title": "title"}, "identity": [],
      "relations": [{"field": "supplier", "target": "Supplier", "property": "hasSupplier"}, {"field": "invoice", "target": "Invoice", "property": "hasInvoice"}]}),
]
DOC = """# Payment terms
Acme Steel must be paid within 30 days of invoice. Late payments incur a 2% monthly penalty.

# Termination
Either party may terminate the Steel Supply 2026 contract with 90 days written notice.
"""


class Command(BaseCommand):
    help = "Seed the 'Enterprise Demo' ontology (idempotent)."

    def handle(self, *a, **kw):
        o = Ontology.objects.filter(name=NAME).first() or Ontology.objects.create(name=NAME, model=MODEL, context="primesemonto")
        for name, prio, csv, spec in SOURCES:
            s, _ = FabricSource.objects.get_or_create(ontology=o, name=name, defaults={"kind": "file", "priority": prio, "secret_enc": crypto.encrypt_json({})})
            s.config, s.mapping, s.priority = {"inline": {"format": "csv", "text": csv}}, {"entities": [spec]}, prio
            s.save()
            r = sync_source(s, full=True, actor="seed")
            self.stdout.write(f"{name}: {r.status} {r.error}")
        ingest_document(o, title="Steel Supply Agreement", text=DOC, doc_type="contract")
        for r in (dict(name="availableCredit", class_name="Customer", kind="derive", target="availableCredit", expression="creditLimit - sum(Invoice.amount)"),
                  dict(name="lowCredit", class_name="Customer", kind="alert", expression="availableCredit < 1000", message="Credit nearly exhausted", severity="critical")):
            TwinRule.objects.get_or_create(ontology=o, name=r["name"], defaults=r)
        mk = lambda n, **k: Agent.objects.get_or_create(ontology=o, name=n, defaults=k)  # noqa: E731
        mk("Chief", role="supervisor", capabilities=["delegate"], permissions=["read"])
        mk("Researcher", role="research", capabilities=["rag.ask", "rag.retrieve", "ontology.search", "memory"], permissions=["read"])
        mk("DataAgent", role="data", capabilities=["fabric.query"], permissions=["read"])
        mk("Simulator", role="simulation", capabilities=["twin.simulate", "twin.read"], permissions=["read"])
        mk("Operator", role="action", capabilities=["twin.write", "fabric.sync"], permissions=["update", "execute"])
        Policy.objects.get_or_create(ontology=o, name="Customer changes need review", defaults=dict(effect="require_approval", operations=["update"], concepts=["Customer"]))
        self.stdout.write(f"Ready: ontology id {o.id} '{NAME}'. Open it from the ontology picker.")
