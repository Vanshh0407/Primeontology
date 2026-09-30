# Security notes

* **Authentication belongs to the host.** Set `PRIME_ONTOLOGY_IDENTITY = "prime_ontology.identity.django_user_identity"` (or your own
  `f(request) -> {user, role, tenant}`). The header identity (`X-Prime-Role` …) is a *development convenience*; outside `DEBUG` an
  unconfigured deployment is read-only (`viewer`). Unknown/unauthenticated → `none` (403 everywhere).
* **Standalone login:** session cookie (HttpOnly, SameSite=Lax; `DJANGO_SECURE_COOKIES=1` behind HTTPS), 5 failed attempts per user+IP → 5-minute lockout (429), identical error for unknown user / wrong password, API returns 403 without a session.
  The **demo account admin/admin123 is public knowledge** — seed it only for demos (`PRIME_SEED_DEMO_USER=0` in Docker otherwise). Lockout uses the local-memory cache: use a shared cache (Redis) with multiple workers.
* **Roles:** viewer < editor < reviewer < admin. editor: edit/commit/submit · reviewer: approve/reject · admin: publish/rollback/delete.
  Four-eyes rule: a version's author cannot approve/reject it.
* **Tenancy:** ontologies carry a tenant; every query is filtered by the identity's tenant (404 across tenants). Tenant comes from the host user, never from a client header.
* **CSRF:** with host auth the API requires the custom header `X-Prime-Client` on writes (sent by the SDK; browsers cannot add it cross-site).
* **Database credentials** entered in the UI are used for that request only, never stored/logged/echoed (tested). The endpoint lets a user make the *server* connect to a host they choose → set
  `PRIME_ONTOLOGY_ALLOWED_DB_HOSTS = ["db.internal", ...]` in production (SSRF control). Only `information_schema` metadata is read.
* **Uploads:** 25 MB cap; XML parsed with the stdlib parser (external entities not resolved; tested); files are parsed, never executed.
* **SPARQL** is read-only (SELECT/ASK/CONSTRUCT/DESCRIBE); update/`SERVICE`/`LOAD` keywords rejected; 1000-row cap.
* **AI** proposals are never applied silently: proposal → diff → explicit approval → re-validated → audit event.
* Not done: rate limiting, upload virus scanning, query timeouts for pathological SPARQL, secrets management — provide at the host/gateway.
