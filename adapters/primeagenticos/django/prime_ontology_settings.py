"""PrimeAgentic OS — settings to add to the host Django project (the only host-specific backend file).

1. pip install ./prime-ontology/backend            (or copy the prime_ontology/ package)
2. INSTALLED_APPS += ["prime_ontology"]  and  python manage.py migrate
3. urls.py:  path("api/v1/ontology/", include("prime_ontology.urls"))
4. from .prime_ontology_settings import *   (or paste the values below into settings.py)
"""

PRIME_ONTOLOGY_IDENTITY = "prime_ontology.identity.django_user_identity"  # uses the host's request.user
PRIME_ONTOLOGY_ROLE_MAP = {
    "Agent Builders": "editor",
    "Agent Governance": "reviewer"
}
PRIME_ONTOLOGY_AUTHENTICATED_ROLE = "editor"          # authenticated users without a mapped group
PRIME_ONTOLOGY_TENANT_ATTR = "workspace_id"              # attribute path on request.user isolating tenants
# PRIME_ONTOLOGY_ALLOWED_DB_HOSTS = ["db.internal"]   # recommended: restrict which databases users may connect to
# ANTHROPIC_API_KEY (env) enables the optional LLM path of the AI assistant; everything works without it.
