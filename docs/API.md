# API (`/api/v1/ontology/…`, with or without trailing slash)

Errors: `{"error": "..."}` with 400 (validation) · 403 (role / CSRF header / DB-host allow-list) · 404 · 413.

| Area | Endpoint |
|---|---|
| System | `GET health` · `GET supported` |
| Sources | `POST inspect {type:"mysql"\|"sql", config}` · `POST generate {type, config, save?}` · `POST ingest` (multipart `file`) → reviewable candidate, nothing saved |
| Ontologies | `GET ""` · `POST ""` (create, or `mergeInto`) · `GET/PUT/DELETE <id>` · `GET <id>/export?format=turtle\|xml\|json-ld\|nt[&version=]` · `GET <id>/graph` |
| Validation | `GET <id>/validate` · `GET <id>/reason?profile=owlrl\|rdfs` · `GET <id>/shapes` · `POST <id>/shacl {data?}` |
| Explore / query | `GET <id>/search?q=` · `GET <id>/concepts/<name>` · `GET <id>/path?from=&to=` · `POST <id>/query {kind:"sparql"\|"natural", text}` · `GET/POST <id>/queries` · `DELETE <id>/queries/<qid>` |
| Mapping | `POST <id>/mapping/extract` (files) · `POST <id>/mapping/propose {sources}` · `GET/POST <id>/mappings` · `PUT/DELETE <id>/mappings/<mid>` · `POST <id>/mappings/<mid>/commit` |
| Governance | `GET/POST <id>/versions` · `GET <id>/versions/<n>` · `POST <id>/versions/<n>/(submit\|approve\|reject\|publish\|rollback)` · `GET <id>/diff?from=&to=` · `GET <id>/audit` |
| AI | `POST <id>/ai {prompt}` → proposal + diff · `POST <id>/ai/apply {ops}` |
| Agents | `GET/PUT <id>/agentic` · `POST <id>/agent/plan {request, role}` · `GET <id>/agent/context?q=` |
| Embedding | `GET embedded/manifest` · `GET embedded/context?host=&ontology_id=` · `POST embedded/event` |

Model shape: `{classes:[{name,label,comment,parents,equivalentTo,disjointWith,synonyms,source,evidence}], dataProperties:[{name,domain,datatype,required,identifier,min/maxCardinality,source}], objectProperties:[{name,domain,range,cardinality,inverseOf,…}], individuals?, layout?, agentic?}`.
