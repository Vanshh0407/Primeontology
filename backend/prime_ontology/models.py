from django.db import models

STATUSES = [("draft", "Draft"), ("review", "In review"), ("approved", "Approved"), ("published", "Published"), ("archived", "Archived")]


class Ontology(models.Model):
    """Canonical ontology record. `model` is the host-neutral working copy
    (classes, dataProperties, objectProperties + provenance)."""

    name = models.CharField(max_length=200)
    base_iri = models.CharField(max_length=500, default="http://primeontology.ai/onto#")
    context = models.CharField(max_length=50, blank=True, default="")  # host app tag
    tenant = models.CharField(max_length=100, blank=True, default="")
    source_type = models.CharField(max_length=50, blank=True, default="")
    model = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUSES, default="draft")
    current_version = models.CharField(max_length=20, blank=True, default="")  # last published/committed
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Optimistic concurrency: bumped only when `model` content changes (not on status/metadata changes), so a reviewer
    # approving a version does not make an editor's unsaved model edits conflict.
    model_revision = models.IntegerField(default=1)
    # Branching: a branch is a full ontology (own versions/workflow) linked to its root, with the model it forked from.
    branch_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="branches")
    branch_name = models.CharField(max_length=100, blank=True, default="")
    base_version = models.CharField(max_length=20, blank=True, default="")
    base_snapshot = models.JSONField(null=True, blank=True)
    merged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name

    @staticmethod
    def _digest(model) -> str:
        import hashlib
        import json

        return hashlib.sha1(json.dumps(model, sort_keys=True, default=str).encode()).hexdigest()

    @classmethod
    def from_db(cls, db, field_names, values):
        obj = super().from_db(db, field_names, values)
        obj._model_digest = cls._digest(obj.model) if "model" in field_names else None
        return obj

    def save(self, *args, **kwargs):
        digest = self._digest(self.model)
        before = getattr(self, "_model_digest", None)
        if before is not None and before != digest:
            self.model_revision += 1
            fields = kwargs.get("update_fields")
            if fields is not None:
                kwargs["update_fields"] = list(set(fields) | {"model_revision"})
        super().save(*args, **kwargs)
        self._model_digest = digest


class OntologyVersion(models.Model):
    """Immutable snapshot."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="versions")
    number = models.CharField(max_length=20)
    snapshot = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUSES, default="draft")
    message = models.CharField(max_length=500, blank=True, default="")
    created_by = models.CharField(max_length=200, blank=True, default="")
    reviewed_by = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        unique_together = [("ontology", "number")]


class AuditEvent(models.Model):
    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, null=True, blank=True, related_name="audit")
    action = models.CharField(max_length=80)
    actor = models.CharField(max_length=200, blank=True, default="")
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class MappingSet(models.Model):
    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="mapping_sets")
    name = models.CharField(max_length=200)
    mappings = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]


class QueryRecord(models.Model):
    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="queries")
    name = models.CharField(max_length=200, blank=True, default="")
    text = models.TextField()
    kind = models.CharField(max_length=20, default="sparql")  # sparql | natural
    saved = models.BooleanField(default=False)
    result_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


# Extension models (R11+). Imported last: they reference Ontology above.
from .fabric import models as _fabric_models  # noqa: E402,F401
from .rag import models as _rag_models  # noqa: E402,F401
from .twin import models as _twin_models  # noqa: E402,F401
from .autonomy import models as _autonomy_models  # noqa: E402,F401
from .osplane import models as _os_models  # noqa: E402,F401
