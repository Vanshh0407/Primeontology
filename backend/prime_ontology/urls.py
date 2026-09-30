from django.urls import path

from . import auth_views as a
from . import views as v

_routes = [
    path("auth/login/", a.auth_login),
    path("auth/logout/", a.auth_logout),
    path("auth/me/", a.auth_me),
    path("health/", v.health),
    path("supported/", v.supported),
    path("inspect/", v.inspect_source),
    path("generate/", v.generate),
    path("ingest/", v.ingest_file),
    path("embedded/manifest/", v.embedded_manifest),
    path("embedded/context/", v.embedded_context),
    path("embedded/event/", v.embedded_event),
    path("", v.ontology_list),
    path("<int:pk>/", v.ontology_detail),
    path("<int:pk>/export/", v.ontology_export),
    path("<int:pk>/graph/", v.graph),
    path("<int:pk>/validate/", v.validate_view),
    path("<int:pk>/reason/", v.reason_view),
    path("<int:pk>/shapes/", v.shapes_view),
    path("<int:pk>/shacl/", v.shacl_view),
    path("<int:pk>/search/", v.search_view),
    path("<int:pk>/concepts/<str:name>/", v.concept_view),
    path("<int:pk>/path/", v.path_view),
    path("<int:pk>/query/", v.query_view),
    path("<int:pk>/queries/", v.queries_view),
    path("<int:pk>/queries/<int:qid>/", v.query_delete),
    path("<int:pk>/mapping/extract/", v.mapping_extract),
    path("<int:pk>/mapping/propose/", v.mapping_propose),
    path("<int:pk>/mappings/", v.mapping_sets),
    path("<int:pk>/mappings/<int:mid>/", v.mapping_set_detail),
    path("<int:pk>/mappings/<int:mid>/commit/", v.mapping_commit),
    path("<int:pk>/versions/", v.versions_view),
    path("<int:pk>/versions/<str:number>/", v.version_detail),
    path("<int:pk>/versions/<str:number>/<str:action>/", v.version_action),
    path("<int:pk>/diff/", v.diff_view),
    path("<int:pk>/audit/", v.audit_view),
    path("<int:pk>/ai/", v.ai_propose),
    path("<int:pk>/ai/apply/", v.ai_apply),
    path("<int:pk>/agentic/", v.agentic_registry),
    path("<int:pk>/agent/plan/", v.agent_plan),
    path("<int:pk>/agent/context/", v.agent_context),
]

# Accept every route with and without the trailing slash: Next.js (and some proxies) strip it, and a Django
# APPEND_SLASH redirect would then loop against the host's rewrite.
urlpatterns = []
for _p in _routes:
    urlpatterns.append(_p)
    route = str(_p.pattern)
    if route.endswith("/"):
        urlpatterns.append(path(route[:-1], _p.callback))
