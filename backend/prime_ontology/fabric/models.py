"""R11 — Enterprise Knowledge Fabric data model.

source ──< run          (one synchronisation attempt)
source ──< record       (a row/object from a source system, mapped to an ontology class; the "staging" layer)
record ──> entity       (entity resolution: many records from many systems -> one semantic entity)
record ──< ref          (a reference from a record to a record of another class, e.g. invoice.customer_id)
entity       = golden record + per-property provenance
event        = lineage / change log (append-only)
"""
from django.db import models

from ..models import Ontology

APP = "prime_ontology"


class FabricSource(models.Model):
    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="fabric_sources")
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=30)  # mysql | rest | odoo | salesforce | sap_odata | file
    config = models.JSONField(default=dict)  # non-secret connection settings
    secret_enc = models.TextField(blank=True, default="")  # Fernet-encrypted JSON of credentials; never returned by the API
    mapping = models.JSONField(default=dict)  # {"entities": [...]}  see fabric/mapping_spec.py
    enabled = models.BooleanField(default=True)
    sync_interval_minutes = models.IntegerField(default=0)  # 0 = manual only
    freshness_sla_minutes = models.IntegerField(default=1440)
    priority = models.IntegerField(default=50)  # survivorship: higher wins
    watermark = models.JSONField(default=dict)  # per-class incremental cursor
    last_status = models.CharField(max_length=20, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    last_synced_at = models.DateTimeField(null=True, blank=True)  # last SUCCESSFUL sync (drives freshness)
    next_sync_at = models.DateTimeField(null=True, blank=True)
    running_since = models.DateTimeField(null=True, blank=True)
    full_every = models.IntegerField(default=24)  # force a full reconcile every N syncs (detects deletions)
    sync_count = models.IntegerField(default=0)
    created_by = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = APP
        ordering = ["name"]
        unique_together = [("ontology", "name")]


class FabricSyncRun(models.Model):
    source = models.ForeignKey(FabricSource, on_delete=models.CASCADE, related_name="runs")
    mode = models.CharField(max_length=12, default="incremental")  # incremental | full
    trigger = models.CharField(max_length=12, default="manual")  # manual | schedule | api
    status = models.CharField(max_length=12, default="running")  # running | succeeded | partial | failed
    stats = models.JSONField(default=dict)
    error = models.TextField(blank=True, default="")
    actor = models.CharField(max_length=200, blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = APP
        ordering = ["-id"]


class FabricEntity(models.Model):
    """The semantic entity: one real-world thing (a customer, a supplier...) however many systems describe it."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="fabric_entities")
    class_name = models.CharField(max_length=100)
    display_name = models.CharField(max_length=300, blank=True, default="")
    canonical = models.JSONField(default=dict)  # golden record {property: value}
    provenance = models.JSONField(default=dict)  # {property: {source, record, external_id, at}}
    status = models.CharField(max_length=10, default="active")  # active | merged | deleted
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="absorbed")
    origin = models.CharField(max_length=10, default="fabric")  # fabric | twin (created directly in the digital twin)
    record_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = APP
        indexes = [models.Index(fields=["ontology", "class_name", "status"]), models.Index(fields=["ontology", "display_name"])]


class FabricRecord(models.Model):
    source = models.ForeignKey(FabricSource, on_delete=models.CASCADE, related_name="records")
    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="fabric_records")
    class_name = models.CharField(max_length=100)
    external_id = models.CharField(max_length=255)
    data = models.JSONField(default=dict)  # mapped properties
    content_hash = models.CharField(max_length=40, blank=True, default="")
    source_updated_at = models.DateTimeField(null=True, blank=True)
    entity = models.ForeignKey(FabricEntity, null=True, blank=True, on_delete=models.SET_NULL, related_name="records")
    link_reason = models.JSONField(default=dict)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_changed_at = models.DateTimeField(null=True, blank=True)
    deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    seen_run = models.IntegerField(null=True, blank=True)

    class Meta:
        app_label = APP
        unique_together = [("source", "class_name", "external_id")]
        indexes = [models.Index(fields=["ontology", "class_name", "deleted"])]


class FabricToken(models.Model):
    """Identity token of a record (e.g. 'email:ann@x.com'). Records sharing a token (within a class) are the same entity."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE)
    class_name = models.CharField(max_length=100)
    token = models.CharField(max_length=190)
    record = models.ForeignKey(FabricRecord, on_delete=models.CASCADE, related_name="tokens")

    class Meta:
        app_label = APP
        indexes = [models.Index(fields=["ontology", "class_name", "token"])]


class FabricRef(models.Model):
    """record --property--> record of another class (resolved lazily: the target may be synced later)."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE)
    from_record = models.ForeignKey(FabricRecord, on_delete=models.CASCADE, related_name="refs_out")
    to_record = models.ForeignKey(FabricRecord, null=True, blank=True, on_delete=models.SET_NULL, related_name="refs_in")
    property = models.CharField(max_length=100)
    target_class = models.CharField(max_length=100)
    target_external_id = models.CharField(max_length=255)
    target_source = models.CharField(max_length=120, blank=True, default="")  # optional: only match records of this source

    class Meta:
        app_label = APP
        unique_together = [("from_record", "property", "target_class", "target_external_id")]
        indexes = [models.Index(fields=["ontology", "target_class", "target_external_id"])]


class FabricEvent(models.Model):
    """Append-only lineage / change log."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="fabric_events")
    kind = models.CharField(max_length=40, db_index=True)
    entity = models.ForeignKey(FabricEntity, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    record = models.ForeignKey(FabricRecord, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    source = models.ForeignKey(FabricSource, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    run = models.ForeignKey(FabricSyncRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    payload = models.JSONField(default=dict)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        app_label = APP
        ordering = ["-id"]


class FabricConstraint(models.Model):
    """Human decision on identity: two records MUST (or CANNOT) be the same entity. Respected by every future resolution."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE)
    class_name = models.CharField(max_length=100)
    record_a = models.ForeignKey(FabricRecord, on_delete=models.CASCADE, related_name="+")
    record_b = models.ForeignKey(FabricRecord, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=6)  # must | cannot
    created_by = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP


class FabricSuggestion(models.Model):
    """Fuzzy match proposal. NEVER merged automatically: a reviewer accepts or rejects it."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="fabric_suggestions")
    class_name = models.CharField(max_length=100)
    entity_a = models.ForeignKey(FabricEntity, on_delete=models.CASCADE, related_name="+")
    entity_b = models.ForeignKey(FabricEntity, on_delete=models.CASCADE, related_name="+")
    score = models.FloatField()
    reasons = models.JSONField(default=list)
    status = models.CharField(max_length=10, default="pending")  # pending | accepted | rejected
    decided_by = models.CharField(max_length=200, blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        ordering = ["-score"]


class FabricDrift(models.Model):
    """Schema drift: a source delivers a field the ontology does not know. Proposed as an ontology change, never auto-applied."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="fabric_drift")
    source = models.ForeignKey(FabricSource, on_delete=models.CASCADE, related_name="drift")
    class_name = models.CharField(max_length=100)
    field = models.CharField(max_length=120)
    inferred_type = models.CharField(max_length=20, default="string")
    sample = models.JSONField(default=list)
    status = models.CharField(max_length=10, default="pending")  # pending | applied | dismissed
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        unique_together = [("source", "class_name", "field")]


class FabricState(models.Model):
    """Version counter: bumped whenever fabric content changes (invalidates the materialised graph cache)."""

    ontology = models.OneToOneField(Ontology, on_delete=models.CASCADE, related_name="fabric_state")
    version = models.IntegerField(default=0)


class FabricInbox(models.Model):
    """Records pushed by a SaaS system / webhook, waiting for the next sync."""

    source = models.ForeignKey(FabricSource, on_delete=models.CASCADE, related_name="inbox")
    class_name = models.CharField(max_length=100)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        indexes = [models.Index(fields=["source", "processed", "class_name"])]


def property_provenance(entity) -> dict:
    """Per-property provenance only. entity.provenance also carries a "__conflicts" list when systems disagree - never iterate it blindly."""
    return {k: v for k, v in (entity.provenance or {}).items() if isinstance(v, dict)}
