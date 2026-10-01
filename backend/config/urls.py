from django.urls import include, path

from prime_ontology.autonomy import views as ag
from prime_ontology.fabric import views as fabric_views
from prime_ontology.osplane import views as osv

_global = [
    # R11 - global alias of the per-ontology fabric API
    path("api/v1/fabric/sources/", fabric_views.global_sources),
    # R14 - autonomous platform alias (ontologyId in the query/body; optional when the tenant has a single ontology)
    path("api/v1/autonomous/manifest/", ag.autonomous_manifest),
    path("api/v1/autonomous/agents/", ag.make_alias(ag.agents)),
    path("api/v1/autonomous/plan/", ag.make_alias(ag.agent_plan)),
    path("api/v1/autonomous/authorize/", ag.make_alias(ag.agent_authorize)),
    path("api/v1/autonomous/run/", ag.make_alias(ag.agent_execute)),
    path("api/v1/autonomous/recover/", ag.make_alias(ag.agent_recover)),
    # R15 - Semantic Enterprise OS control plane
    path("api/v1/os/manifest/", osv.manifest),
    path("api/v1/os/capabilities/", osv.capabilities),
    path("api/v1/os/snapshot/", osv.snapshot),
    path("api/v1/os/policies/", osv.policies),
    path("api/v1/os/policies/<int:pid>/", osv.policy_detail),
    path("api/v1/os/evaluate/", osv.evaluate),
    path("api/v1/os/audit/", osv.audit),
    path("api/v1/os/hosts/", osv.hosts),
    path("api/v1/os/ask/", osv.ask),
]

urlpatterns = [path("api/v1/ontology/", include("prime_ontology.urls"))]
for _p in _global:  # every route also without the trailing slash (same reason as prime_ontology.urls)
    urlpatterns.append(_p)
    route = str(_p.pattern)
    if route.endswith("/"):
        urlpatterns.append(path(route[:-1], _p.callback))
