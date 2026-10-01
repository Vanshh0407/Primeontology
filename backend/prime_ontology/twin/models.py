from django.db import models

from ..fabric.models import FabricEntity
from ..models import Ontology

APP = "prime_ontology"


class TwinAttributeHistory(models.Model):
    """Temporal knowledge: every value an attribute had, with the interval it was valid for. valid_to NULL = current."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="twin_history")
    entity = models.ForeignKey(FabricEntity, on_delete=models.CASCADE, related_name="history")
    property = models.CharField(max_length=100)
    value = models.JSONField(null=True)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=20, default="fabric")  # fabric | event | manual | twin
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        indexes = [models.Index(fields=["entity", "property", "valid_from"]), models.Index(fields=["ontology", "valid_to"])]


class TwinEvent(models.Model):
    """Append-only event stream: what happened to the enterprise and when."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="twin_events")
    entity = models.ForeignKey(FabricEntity, null=True, blank=True, on_delete=models.SET_NULL, related_name="twin_events")
    event_type = models.CharField(max_length=80, db_index=True)
    occurred_at = models.DateTimeField(db_index=True)
    payload = models.JSONField(default=dict)
    source = models.CharField(max_length=60, default="api")
    actor = models.CharField(max_length=200, blank=True, default="")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        ordering = ["-occurred_at", "-id"]


class TwinProcess(models.Model):
    """A business process as a state machine over entities of one class (e.g. Invoice: draft -> sent -> paid)."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="twin_processes")
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=500, blank=True, default="")
    entity_class = models.CharField(max_length=100)
    states = models.JSONField(default=list)
    initial = models.CharField(max_length=60)
    transitions = models.JSONField(default=list)  # [{"name": "send", "from": "draft", "to": "sent"}]
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        unique_together = [("ontology", "name")]


class TwinProcessInstance(models.Model):
    process = models.ForeignKey(TwinProcess, on_delete=models.CASCADE, related_name="instances")
    entity = models.ForeignKey(FabricEntity, on_delete=models.CASCADE, related_name="process_instances")
    state = models.CharField(max_length=60)
    history = models.JSONField(default=list)  # [{"state", "transition", "at", "actor"}]
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = APP
        unique_together = [("process", "entity")]


class TwinAsset(models.Model):
    """Physical / logical assets (warehouse, machine, contract file, system) forming a hierarchy and linked to entities."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="twin_assets")
    name = models.CharField(max_length=200)
    asset_type = models.CharField(max_length=60, default="asset")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    entity = models.ForeignKey(FabricEntity, null=True, blank=True, on_delete=models.SET_NULL, related_name="assets")
    attributes = models.JSONField(default=dict)
    status = models.CharField(max_length=30, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP


class TwinRule(models.Model):
    """Business logic of the twin: derived attributes ('availableCredit = creditLimit - sum(Invoice.amount)') and alerts."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="twin_rules")
    name = models.CharField(max_length=120)
    class_name = models.CharField(max_length=100)
    kind = models.CharField(max_length=10)  # derive | alert
    target = models.CharField(max_length=100, blank=True, default="")  # derive: the derived property name
    expression = models.CharField(max_length=600)
    message = models.CharField(max_length=300, blank=True, default="")
    severity = models.CharField(max_length=10, default="warning")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        unique_together = [("ontology", "name")]
