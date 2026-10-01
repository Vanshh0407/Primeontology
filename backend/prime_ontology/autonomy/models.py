from django.db import models

from ..models import Ontology

APP = "prime_ontology"


class Agent(models.Model):
    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="agents")
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=20, default="custom")  # supervisor | research | data | simulation | action | custom
    description = models.CharField(max_length=500, blank=True, default="")
    capabilities = models.JSONField(default=list)  # ["rag.ask", "twin.simulate", ...] what it is able to do
    permissions = models.JSONField(default=list)  # operations it may perform: ["read", "update", ...]
    scope = models.JSONField(default=list)  # ontology classes it may touch (empty = all)
    tools = models.JSONField(default=list)  # tool names it may call
    goals = models.JSONField(default=list)
    policies = models.JSONField(default=list)  # names of extra policies bound to this agent (informational; the engine matches by agent name)
    budget = models.JSONField(default=dict)  # {"maxSteps": 20, "maxToolCalls": 20, "maxCost": 50}
    enabled = models.BooleanField(default=True)
    created_by = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        unique_together = [("ontology", "name")]


class AgentTool(models.Model):
    """Governed tool registry. Built-in tools are always present (not stored); this holds registered API/MCP tools."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="agent_tools")
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=10, default="api")  # api | mcp
    description = models.CharField(max_length=500, blank=True, default="")
    spec = models.JSONField(default=dict)  # api: {"url", "method"} | mcp: {"server": "<registered server name>", "tool": "<remote tool>"}
    input_schema = models.JSONField(default=dict)
    operation = models.CharField(max_length=10, default="read")  # read | create | update | delete | execute
    concepts = models.JSONField(default=list)
    risk = models.FloatField(null=True, blank=True)  # overrides the operation's default risk
    external = models.BooleanField(default=False)
    status = models.CharField(max_length=10, default="pending")  # pending (discovered, not governed yet) | approved | disabled
    cost = models.FloatField(default=1.0)
    approved_by = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        unique_together = [("ontology", "name")]


class McpServer(models.Model):
    """A registered MCP server (stdio). Only commands on the server-side allow-list can be started."""

    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="mcp_servers")
    name = models.CharField(max_length=120)
    command = models.JSONField(default=list)  # argv, validated against settings.PRIME_ONTOLOGY_MCP_COMMANDS
    discovered_at = models.DateTimeField(null=True, blank=True)
    server_info = models.JSONField(default=dict)
    last_error = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        app_label = APP
        unique_together = [("ontology", "name")]


class AgentMemory(models.Model):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="memories")
    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, default="episodic")  # episodic (what happened) | semantic (facts/lessons)
    text = models.TextField()
    concepts = models.JSONField(default=list)
    entities = models.JSONField(default=list)
    embedding = models.BinaryField(null=True, blank=True)
    embedding_provider = models.CharField(max_length=40, blank=True, default="")
    run_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        ordering = ["-id"]


class AgentRun(models.Model):
    ontology = models.ForeignKey(Ontology, on_delete=models.CASCADE, related_name="agent_runs")
    goal = models.TextField()
    status = models.CharField(max_length=20, default="planned")  # planned|running|awaiting_approval|succeeded|partial|failed|rejected|cancelled
    supervisor = models.ForeignKey(Agent, null=True, blank=True, on_delete=models.SET_NULL, related_name="supervised_runs")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    depth = models.IntegerField(default=0)
    plan = models.JSONField(default=dict)  # planner output (concepts, intent, notes)
    result = models.JSONField(default=dict)
    evaluation = models.JSONField(default=dict)
    cost = models.FloatField(default=0)
    tool_calls = models.IntegerField(default=0)
    requested_by = models.CharField(max_length=200, blank=True, default="")
    requester_role = models.CharField(max_length=12, default="viewer")
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = APP
        ordering = ["-id"]


class AgentStep(models.Model):
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="steps")
    key = models.CharField(max_length=40)
    ordinal = models.IntegerField(default=0)
    agent = models.ForeignKey(Agent, null=True, blank=True, on_delete=models.SET_NULL, related_name="steps")
    title = models.CharField(max_length=300, blank=True, default="")
    tool = models.CharField(max_length=120)
    args = models.JSONField(default=dict)
    depends_on = models.JSONField(default=list)
    operation = models.CharField(max_length=10, default="read")
    concepts = models.JSONField(default=list)
    status = models.CharField(max_length=20, default="pending")  # pending|awaiting_approval|running|succeeded|failed|skipped|rejected|blocked
    decision = models.JSONField(default=dict)  # policy engine output
    contract = models.JSONField(default=dict)  # execution contract (binds tool+args to the approval)
    output = models.JSONField(null=True)
    error = models.TextField(blank=True, default="")
    attempts = models.IntegerField(default=0)
    cost = models.FloatField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = APP
        ordering = ["ordinal", "id"]
        unique_together = [("run", "key")]


class ApprovalRequest(models.Model):
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="approvals")
    step = models.ForeignKey(AgentStep, on_delete=models.CASCADE, related_name="approvals")
    requested_by = models.CharField(max_length=200)
    reason = models.TextField(blank=True, default="")
    risk = models.JSONField(default=dict)
    required = models.IntegerField(default=1)
    decisions = models.JSONField(default=list)  # [{"by", "decision", "comment", "at"}]
    status = models.CharField(max_length=10, default="pending")  # pending | approved | rejected
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        ordering = ["-id"]


class AgentEvent(models.Model):
    """Append-only execution trace."""

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="trace")
    step = models.ForeignKey(AgentStep, null=True, blank=True, on_delete=models.CASCADE, related_name="trace")
    kind = models.CharField(max_length=30)
    detail = models.JSONField(default=dict)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = APP
        ordering = ["id"]
