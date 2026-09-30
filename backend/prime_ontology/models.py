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

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


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
