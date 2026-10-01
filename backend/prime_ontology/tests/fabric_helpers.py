from prime_ontology.fabric import crypto
from prime_ontology.fabric.models import FabricSource
from prime_ontology.models import Ontology

MODEL = {
    "classes": [{"name": "Customer", "label": "Customer", "parents": []}, {"name": "Supplier", "label": "Supplier", "parents": []},
                {"name": "Invoice", "label": "Invoice", "parents": []}, {"name": "Contract", "label": "Contract", "parents": []},
                {"name": "Ticket", "label": "Ticket", "parents": []}],
    "dataProperties": [
        {"name": "customerName", "domain": "Customer", "datatype": "string"}, {"name": "email", "domain": "Customer", "datatype": "string"},
        {"name": "phone", "domain": "Customer", "datatype": "string"}, {"name": "city", "domain": "Customer", "datatype": "string"},
        {"name": "creditLimit", "domain": "Customer", "datatype": "decimal"}, {"name": "customerId", "domain": "Customer", "datatype": "string"},
        {"name": "supplierName", "domain": "Supplier", "datatype": "string"},
        {"name": "amount", "domain": "Invoice", "datatype": "decimal"}, {"name": "overdueDays", "domain": "Invoice", "datatype": "integer"},
        {"name": "status", "domain": "Invoice", "datatype": "string"},
        {"name": "title", "domain": "Contract", "datatype": "string"}, {"name": "active", "domain": "Contract", "datatype": "boolean"}],
    "objectProperties": [
        {"name": "hasCustomer", "domain": "Invoice", "range": "Customer", "cardinality": "many-to-one"},
        {"name": "hasSupplier", "domain": "Contract", "range": "Supplier", "cardinality": "many-to-one"},
        {"name": "hasInvoice", "domain": "Contract", "range": "Invoice", "cardinality": "many-to-many"},
        {"name": "hasCustomer", "domain": "Ticket", "range": "Customer", "cardinality": "many-to-one"}],
}


def make_ontology(name="Fabric Test", tenant="", model=None):
    import copy

    return Ontology.objects.create(name=name, model=copy.deepcopy(model or MODEL), tenant=tenant)


def customer_spec(id_field="id", fields=None, identity=None, **extra):
    return {"class": "Customer", "id": id_field,
            "fields": fields or {"name": "customerName", "email": "email", "phone": "phone", "city": "city"},
            "identity": identity if identity is not None else [{"keys": ["email"], "normalize": "email"}, {"keys": ["phone"], "normalize": "phone"}], **extra}


def make_source(ontology, name, csv_text, spec=None, priority=50, kind="file", fmt="csv", **kw):
    mapping = {"entities": [spec or customer_spec()]}
    src = FabricSource.objects.create(ontology=ontology, name=name, kind=kind, config={"inline": {"format": fmt, "text": csv_text}} if kind == "file" else kw.pop("config", {}),
                                      mapping=mapping, priority=priority, secret_enc=crypto.encrypt_json(kw.pop("secrets", {})), **kw)
    return src


def set_csv(source, csv_text, fmt="csv"):
    source.config = {"inline": {"format": fmt, "text": csv_text}}
    source.save()
