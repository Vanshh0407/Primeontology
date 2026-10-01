from django.db import models

from ..models import Ontology

APP = "prime_ontology"


class Policy(models.Model):
    """Enterprise policy: who (agents/roles) may do what (operations) on which concepts/tools, at which risk, and with what outcome."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="os_policies")
    name = models.CharField(max_length=120)
    scope = models.CharField(max_length=12, default="agent")  # agent | tool | approval | data | action
    effect = models.CharField(max_length=20)  # allow | require_approval | deny
    description = models.CharField(max_length=500, blank=True, default="")
    actors = models.JSONField(default=dict)  # {"roles": [...], "agents": [...], "actorTypes": ["agent"]} (empty = everyone)
    operations = models.JSONField(default=list)  # ["read","create",...]  (empty or "*" = any)
    concepts = models.JSONField(default=list)  # ontology classes (sub-classes are covered)
    tools = models.JSONField(default=list)  # tool names
    risk_at_least = models.CharField(max_length=10, blank=True, default="")  # low|medium|high|critical
    classifications = models.JSONField(default=list)  # data classifications this applies to
    approvals_required = models.IntegerField(default=1)  # for require_approval: how many distinct approvers
    priority = models.IntegerField(default=100)
    enabled = models.BooleanField(default=True)
    created_by = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        unique_together = [("ontology", "name")]
        ordering = ["priority", "id"]


class HostApp(models.Model):
    """A host application (UniContractAI, PrimeSemOnto, PrimeAgentic OS, ...) registered with the semantic OS."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="host_apps")
    key = models.CharField(max_length=60)
    name = models.CharField(max_length=120)
    capabilities = models.JSONField(default=list)
    token_hash = models.CharField(max_length=64, blank=True, default="")  # sha256 of the service token; the token itself is shown once
    token_hint = models.CharField(max_length=8, blank=True, default="")
    role = models.CharField(max_length=12, default="editor")  # the role a request authenticated with this token acts with
    enabled = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        unique_together = [("ontology", "key")]
