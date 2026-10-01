# API (`/api/v1/ontology/…`, with or without trailing slash)

Errors: `{"error": "..."}` with 400 (validation) · 403 (role / CSRF header / DB-host allow-list) · 404 · 413.

| Area | Endpoint |
|---|---|
| System | `GET health` · `GET supported` |
| Sources | `POST ingest-url {url, authorization?}` (admin-enabled) · `POST ingest` also takes `records=N` and `ai=1` · `POST generate` takes `sampleRows` · `POST inspect {type:"mysql"\|"sql", config}` · `POST generate {type, config, save?}` · `POST ingest` (multipart `file`) → reviewable candidate, nothing saved |
| Ontologies | `GET ""` · `POST ""` (create, or `mergeInto`) · `GET/PUT/DELETE <id>` · `GET <id>/export?format=turtle\|xml\|json-ld\|nt[&version=]` · `GET <id>/graph` |
| Validation | `GET <id>/validate` · `GET <id>/reason?profile=owlrl\|rdfs` · `GET <id>/shapes` · `POST <id>/shacl {data?}` |
| Explore / query | `GET <id>/search?q=` · `GET <id>/concepts/<name>` · `GET <id>/path?from=&to=` · `POST <id>/query {kind:"sparql"\|"natural", text}` · `GET/POST <id>/queries` · `DELETE <id>/queries/<qid>` |
| Mapping | `POST <id>/mapping/extract` (files) · `POST <id>/mapping/propose {sources}` · `GET/POST <id>/mappings` · `PUT/DELETE <id>/mappings/<mid>` · `POST <id>/mappings/<mid>/commit` |
| Branches | `GET/POST <id>/branches {name, fromVersion?}` · `POST <id>/merge {sourceId, dryRun, resolutions:{conflictId:'ours'\|'theirs'}, allowErrors?}` |
| Concurrency | `PUT <id>` accepts `baseRevision` (409 `{code:'conflict', lastEditor, overwriteWouldChange}`) and `force` |
| Governance | `GET/POST <id>/versions` · `GET <id>/versions/<n>` · `POST <id>/versions/<n>/(submit\|approve\|reject\|publish\|rollback)` · `GET <id>/diff?from=&to=` · `GET <id>/audit` |
| AI | `POST <id>/ai {prompt}` → proposal + diff · `POST <id>/ai/apply {ops}` |
| Agents | `GET/PUT <id>/agentic` · `POST <id>/agent/plan {request, role}` · `GET <id>/agent/context?q=` |
| Embedding | `GET embedded/manifest` · `GET embedded/context?host=&ontology_id=` · `POST embedded/event` |

`GET supported` reports `ocr`, `urlSources`, `embeddings`, `aiConfigured`, `sampleRecordsMax`.

Model shape: `{classes:[{name,label,comment,parents,equivalentTo,disjointWith,synonyms,source,evidence}], dataProperties:[{name,domain,datatype,required,identifier,min/maxCardinality,source}], objectProperties:[{name,domain,range,cardinality,inverseOf,…}], individuals?, layout?, agentic?}`.


## R11–R15 endpoints (all under `/api/v1/ontology/{id}/` unless noted; roles: viewer reads, editor writes/syncs, reviewer decides, admin manages)
- Fabric: `fabric/` snapshot · `fabric/sync/` · `fabric/sources/` (+`{sid}/` `test` `discover` `sync` `upload` `suggest-mapping`) · `fabric/entities/` (+`{eid}/`, `lineage`) · `fabric/suggestions/` (+`{sid}/decide/`) · `fabric/drift/` (+`{did}/`) · `fabric/sparql/` · `fabric/export/` · `fabric/runs/` · `fabric/events/`; global alias `GET/POST /api/v1/fabric/sources/`.
- RAG: `rag/documents/` (+`{did}/`) · `rag/retrieve/` · `rag/ask/` · `rag/reindex/`.
- Twin: `twin/` (GET snapshot `?asOf=`, POST create entity) · `twin/events/` · `twin/entities/{eid}/history/` · `twin/rules/` · `twin/processes/` · `twin/assets/` · `twin/simulate/`.
- Agents: `agents/` (+`{aid}/`, `{aid}/memory/`) · `agents/plan/` · `agents/authorize/` · `agents/execute/` · `agents/resume/` · `agents/approve/` · `agents/approvals/` · `agents/recover/` · `agents/cancel/` · `agents/runs/` (+`{run_id}/`) · `agents/tools/` (+`{name}/govern/`) · `agents/mcp/` (+`{sid}/discover/`).
  Alias (`ontologyId` in query/body): `/api/v1/autonomous/manifest|agents|plan|authorize|run|recover/`.
- OS (`ontologyId` in query/body): `/api/v1/os/manifest|capabilities|snapshot|policies|evaluate|audit|hosts|ask/`. Hosts authenticate with `X-Prime-Service-Token`.
