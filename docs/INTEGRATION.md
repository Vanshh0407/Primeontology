# Embedding into UniContractAI / PrimeSemOnto / PrimeAgentic OS

Same module, three hosts. Per host you change **only** `prime-ontology.config.js` (frontend) and `prime_ontology_settings.py` (Django).

```bash
python scripts/integrate.py --host unicontractai --target /path/to/unicontractai [--frontend-dir frontend] [--backend-dir backend]
```
It copies the Django app + host settings, the React module (`vendor/prime-ontology`), `app/ontology/page.jsx`, the config and a `next.config` snippet. It never overwrites existing files without `--force`; `--dry-run` shows what it would do.

## Backend (Django)
1. `INSTALLED_APPS += ["prime_ontology"]`; `from .prime_ontology_settings import *`; `python manage.py migrate` (MySQL).
2. `urls.py`: `path("api/v1/ontology/", include("prime_ontology.urls"))`.
3. Auth: `PRIME_ONTOLOGY_IDENTITY = "prime_ontology.identity.django_user_identity"` maps the host's `request.user` to a role
   (superuser → admin; group map; default editor) and a tenant (`PRIME_ONTOLOGY_TENANT_ATTR`). Any custom resolver `f(request) -> {user, role, tenant}` also works.
4. Requires `django.contrib.sessions` and `AuthenticationMiddleware` (or your JWT middleware that sets `request.user`).
5. Recommended: `PRIME_ONTOLOGY_ALLOWED_DB_HOSTS = [...]` (see SECURITY.md).

## Frontend (Next.js, App Router)
1. `npm install ./vendor/prime-ontology` (brings `@xyflow/react`, `@dagrejs/dagre`; peers: MUI 5, React 18).
2. Merge `next.config.prime-ontology.snippet.js` (`transpilePackages`, rewrite of `/api/v1/ontology/*` to Django, `skipTrailingSlashRedirect`).
3. Open `/ontology[?ontology=<id>]`. The component inherits the host's MUI theme (pass `theme` only to override) and takes roles from the backend.

**The host controls:** branding/theme, navigation, authentication, tenant, permissions, height. **The workbench controls:** everything ontology.

**Events → host:** `onEvent({type, host, ontologyId, payload})` for `ontology.opened|saved|published`, `concept.selected`, `import.completed`, `mapping.committed` (also recorded in the audit trail).

**PrimeAgentic OS** additionally gets the *Agents* tab, `python manage.py mcp_server [--tenant T]` for MCP-capable agents, and `POST /api/v1/ontology/<id>/agent/plan/` for the semantic planner.

## Verified
A scratch Next.js 14 host was built with each of the three adapters (`next build`), served, and driven in Chrome: each shows its own context and title, PrimeAgentic OS alone shows the Agents tab, the graph renders from the live API, and there are no page errors.
`python scripts/test_adapters.py` guarantees the page code is byte-identical across hosts.
