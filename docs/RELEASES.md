# Status vs. the plan (PrimeOntology.txt)

Legend: ✅ built **and verified** · 🟡 built, limited, or verified only partly · ❌ not built.
Verification: **B** = backend automated tests (127) · **U** = frontend unit tests (13) · **E** = real-browser E2E in Chrome (16 smoke steps + 14 feature steps, live API + MySQL; the smoke suite also ran against the Docker stack) · **L** = live MySQL (8.0 local; 8.4, MariaDB 10.11 and 11 full suite in Docker; 5.7 as a source) · **H** = built and rendered inside a real Next.js 14 host.

## Deviations you should know about
1. **PostgreSQL was removed** on request. MySQL is the only live database connector (the plan also listed PostgreSQL). SQL *scripts* are parsed generically (MySQL dialect tested).
2. **No OrionBelt code is used.** Both repos are BSL 1.1 and forbid embedding in commercial products without a licence. See `ORIONBELT_AUDIT.md`.
3. I did not have the source of the three real products. Integration was proven in a scratch **Next.js 14 (App Router)** host and a stock Django project; the adapters may need small changes for the products' actual layouts and auth.

## R1 — Database → Ontology ✅ B L E
MySQL connect · schema inspection (tables, columns, PK/FK) · table→class, column→datatype property, FK→object property, junction table→many-to-many · SQL→XSD types ·
semantic naming (`SALES_ORDER`→`SalesOrder`, `customer_id`→`hasCustomer`) · relationship **inference** when a DB declares no FKs (ORM-managed schemas; flagged "inferred") ·
NOT NULL / PK → cardinality restrictions · provenance (database/table/column) on every element · review before save · export Turtle / RDF-XML (OWL) / JSON-LD / N-Triples · persistence.
Live-tested on `primeontology_demo` and, read-only, on 178- and 102-table databases (schema → ontology in under 1 s).
🟡 "AI-assisted semantic naming" is rule-based (naming heuristics), not an LLM.

## R2 — Visual workbench ✅ U E
Create / rename / delete class · add / edit / delete data and object properties · domain / range · cardinality (min / max / required) · inheritance (cycle-safe) · equivalent / disjoint classes · synonyms · descriptions · inverse properties ·
drag-to-connect (relationship or inheritance) · explorer tree · graph search and filter · zoom / pan / minimap · 4 layouts (top-down, left-right, circular, grid) · positions saved with the ontology · undo · Ctrl+S · unsaved-changes guard · read-only mode for viewers.
✅ **Groups**: assign a group per class, auto-group by connected cluster or by source, colour stripes, dashed frames, focus filter, "Cluster by group" layout. ✅ **Individuals editor**: add / rename / delete records, typed value fields, links to other individuals, validation (E).
✅ **Large ontologies**: 1,000 classes open in ~1.3 s, 2,000 in ~2.8 s (fast cluster-grid layout, compact rendering, paged Explorer); hierarchical layout is offered on request with a "computing" notice because it takes seconds beyond ~1,000 classes.

## R3 — Universal data → ontology ✅ B E
CSV, TSV, Excel (multi-sheet), JSON (nested → related tables), XML, YAML, SQL DDL, RDF/XML, OWL, Turtle, N-Triples, N-Quads, JSON-LD, N3, TriG. Type inference, PK guess, FK inference across files and sheets,
source→ontology mapping table, merge into an existing ontology (existing definitions win, provenance accumulates).
✅ **Parquet** and **SQLite database files**. ✅ **REST/JSON API** source — *disabled unless an administrator allow-lists hosts* (SSRF-safe; B). ✅ **Sample records** (opt-in): up to N rows per table (hard cap 500) become individuals, with foreign keys turned into links; sensitive-looking columns (password, token, key, card…) are never copied; MySQL, SQLite, Parquet, CSV, Excel. With records, plain-word questions return real rows and the plan's "suppliers in Maharashtra" example works (B E).
❌ SQL Server / Oracle / PostgreSQL connections (MySQL only, by your decision) · GraphQL / SAP / Salesforce / ServiceNow / SharePoint / S3 / warehouse connectors (the plan marks these "eventually").

## R4 — Document → ontology 🟡 B E
PDF (text), DOCX, PPTX, TXT, Markdown, HTML → sections (page / heading) → concepts, relationships and data properties with **evidence: page + section + snippet** ("Found in …: Page 2, Section 'PAYMENT TERMS'").
Contract lexicon (Contract, Party, Customer, Supplier, Clause, Obligation, Payment, Term, Jurisdiction, Governing law, Liability, …), defined terms (`("Buyer")`), recurring capitalised phrases, date / amount / law extraction.
🟡 Extraction is **lexicon / pattern based, not ML NER**; quality outside contract-style language will be lower.
🟡 Tested with generated documents, not a corpus of real contracts.
✅ **Scanned PDFs are OCR'd** locally (RapidOCR; optional package) with page numbers kept; mixed text+scan PDFs work; clearly reported when OCR is unavailable (B E). 🟡 OCR accuracy depends on scan quality.

## R5 — AI semantic mapping ✅ (deterministic) B E · 🟡 (LLM)
Abbreviation expansion, synonym groups, token / sequence similarity, datatype compatibility, source↔concept context, explainable reasons, HIGH / MEDIUM / LOW confidence, top-K candidates,
duplicate-field detection, accept / reject / manual override, saved mapping sets, **commit lineage into the ontology only on explicit action**.
Verified with the plan's example: `cust_nm` / `customer_name` / `client_name` → `Customer.customerName`, `Buyer` → `Customer`.
AI assistant: proposal → diff → approve, never silent (B E); templates for contract / sales / agent domains; commands (add class, connect, add property, rename, delete).
🟡 **The LLM path (`llm.py`, Anthropic API) is tested against a local stand-in server** (request shape, auth header, parsing, op validation, fallback on failure, https-only URL rule; B) **but has never run against the real API** (no key). Document extraction can ask the LLM for extra concepts: every one must quote text that really occurs in the document (verified server-side) or it is discarded; they are flagged low-confidence (B). Without `ANTHROPIC_API_KEY` everything falls back to rules.
✅ **Semantic embeddings** (BAAI/bge-small-en-v1.5, local ONNX): boost mapping and find concepts with no shared words ("vendor" → Supplier). Loads in the background; until loaded (or if it cannot load) a lexical engine is used and the UI says so. 🟡 It is a *weak* signal for single words (ranks related terms above unrelated ~96% of the time in my test set), so it only boosts partial matches.

## R6 — Validation & reasoning ✅ B E
Health score + errors / warnings / passed · invalid IRI, duplicate class / property / concept / label, missing or invalid domain and range, invalid datatype, orphan class, self-subclass, circular hierarchy, unknown parent,
cardinality conflicts, disjoint-with-ancestor, incompatible range along inheritance, mapping conflicts and missing targets · click an issue to jump to the element ·
RDFS and OWL 2 RL reasoning (owlrl): inferred hierarchy, inferred individual types, consistency (detects disjoint violations) · SHACL shapes generated from the ontology + validation of a data graph (pyshacl).

## R7 — Knowledge explorer & query ✅ B E
Concept card (own + inherited properties, incoming / outgoing relationships, ancestors, sources and provenance, mappings, neighbour graph) · fuzzy semantic search with synonyms · "how are A and B connected" path finder ·
**Query Studio**: read-only SPARQL (examples, results table, click-through to concepts) · natural-language questions → concept grounding → relationship path → generated SPARQL → run · saved queries · history.
🟡 The plan's "…suppliers in Maharashtra" example needs *instance data*. Ontologies generated from a schema contain no individuals, so such a query returns the connecting path and zero rows (the UI says so). Verified with individuals in tests.

## R8 — Versioning & governance ✅ B E
Immutable versions (x.y, major / minor) · diff (added / removed / renamed / changed classes and properties) · Draft → Review → Approved → Published workflow · four-eyes rule · rollback (creates a new version) · superseded versions archived ·
audit trail of every mutation · role-based permissions · editing a published ontology reopens it as a draft.
✅ **Branching & merging**: a branch is a full ontology with its own versions/workflow; three-way merge at element + field level, conflicts need an explicit keep-mine / take-theirs decision, merges that add validation errors are blocked unless forced, every merge audited (B E).

## R9 — Embedded in the three products ✅ (module) H · 🟡 (real hosts)
`<PrimeOntologyWorkbench apiBase ontologyId context permissions getAuthHeaders onEvent theme />` — one component. Host contexts (title, vocabulary, visible tabs): UniContractAI, PrimeSemOnto, PrimeAgentic OS.
Manifest / context / event APIs. **Page code is identical across the three hosts (test-enforced); only a config file differs.** Built and rendered in a real Next.js 14 host for each context.
Host identity, tenant and roles come from Django (`django_user_identity`), with a CSRF header and DB-host allow-list. `scripts/integrate.py` installs backend + frontend into a host repo.
🟡 Not yet run inside the actual UniContractAI / PrimeSemOnto / PrimeAgentic OS repositories (not available to me).

## R10 — Ontology-powered agents ✅ B E
Tool / workflow / API / MCP registry bound to concepts · ontology-level policies (allow / require_approval / deny, by concept incl. subclasses, operation, role) · semantic planner
(request → concepts → path → tool binding → policy decision: ready / partial / needs approval / blocked / unreachable / clarify) · LLM context pack ·
**read-only MCP server** (`python manage.py mcp_server`, 7 tools, tenant-scoped; verified over real stdio on MySQL).
🟡 The planner is deterministic and does not execute tools; PrimeAgentic OS consumes the plan.

## R11 — Enterprise Knowledge Fabric ✅ B (UI built, not browser-tested)
Source registry (MySQL, REST, Odoo, Salesforce, SAP OData v2/v4, file), incremental sync with watermarks, change detection, deterministic entity resolution
(identity tokens + union-find, golden record with survivorship and per-property provenance, human MUST/CANNOT-link, fuzzy matches only as suggestions),
schema-drift proposals, freshness (FRESH/STALE/VERY_STALE/NEVER_SYNCED), append-only lineage, RDF graph + SPARQL, `manage.py fabric_worker`.
Credentials are Fernet-encrypted and never returned; connectors only reach hosts in `PRIME_ONTOLOGY_CONNECTOR_HOSTS`.
**Honest scope:** SAP / Salesforce / Odoo / REST adapters are real protocol implementations tested against a local protocol mock
(`tests/mock_enterprise.py`) — never against a live SAP, Salesforce or Odoo system (no credentials). MySQL adapter: protocol-level only here.

## R12 — Semantic RAG & AI context ✅ B (UI built, not browser-tested)
Ontology-aware chunking, BM25 + vector + graph retrieval with reciprocal-rank fusion, ontology query expansion, structured graph questions
("contracts with suppliers whose invoices were overdue more than 60 days"), classification filtering by role, metadata filters, context-size control,
confidence and grounded / partially grounded / ungrounded classification, abstention, citation verification for LLM answers.
LLM phrasing is optional and tested only against a stub (no API key here); default answers are extractive. Vectors use fastembed when available, else a lexical hash fallback.

## R13 — Semantic Digital Twin ✅ B (UI built, not browser-tested)
Temporal attribute history (late events slotted correctly), event stream, as-of snapshots, process state machines, asset hierarchy, safe rule language
(no `eval`) for derived values and alerts, non-destructive what-if simulation (verified: nothing is written).

## R14 — Autonomous agent platform ✅ B (UI built, not browser-tested)
Agent registry, rule-based multi-agent planner (supervisor / research / data / simulation / action), DAG execution with authorisation per step
(agent permissions + requester role + enterprise policy), four-eyes approvals bound to the exact arguments by an execution contract, delegation with
permission intersection, semantic memory, budgets, retry/recovery, traces, evaluation scores, governed tool registry, MCP client (allow-listed commands only).
The planner is deterministic keyword/ontology logic, not an LLM planner. MCP discovery is tested against a stub stdio server.

## R15 — Semantic Enterprise OS ✅ B (UI built, not browser-tested)
`/api/v1/os/*`: manifest, per-role capabilities, enterprise snapshot, policy CRUD + evaluation (risk-based defaults: high → approval, critical → deny),
audit, host registration with one-time service tokens, cross-module `ask` routing.

## R11–R15 follow-ups ✅ B E
Data-lake adapter (CSV/TSV/JSON/JSONL/Parquet under administrator-approved roots, `PRIME_ONTOLOGY_DATALAKE_ROOTS`; cloud buckets via mounts, no native S3/ADLS/GCS client),
push/webhook source kind for SaaS and iPaaS, domain graph views in the twin (customer, supplier, financial, contract, organization, process, asset),
n8n and BPMN workflow export of agent plans (structure-checked only, not imported into a live n8n/BPM engine), Neo4j Cypher export and vector-DB JSONL export,
per-ontology `/autonomous/*` routes, agent and MCP registration in the UI, `fabric-worker` docker service, left sidebar navigation,
`seed_enterprise_demo` command, browser tests `npm run e2e:enterprise` (11 steps) — they caught and fixed a real crash for entities whose systems disagree.
**Still not done:** nothing was tested against live SAP / Salesforce / Odoo / n8n / BPM / Neo4j systems; LLM answers only against a stub.

## Concurrent editing
✅ Optimistic locking: a stale save is refused (409) with who/when and what overwriting would change; the user can reload theirs, overwrite, or keep editing. Reviewer status changes don't conflict with model edits (B E, two real browser sessions).

## Measured scale (Chrome, this machine, 1.5 relationships per class)
| classes | open workbench | validate tab | Explorer filter |
|---|---|---|---|
| 300 | 1.0 s | 1.2 s | 0.2 s |
| 1,000 | 1.3 s | 3.4 s | 0.3 s |
| 2,000 | 2.8 s | 5.1 s | 1.0 s |
(Before the fixes: 2,000 classes took 35 s to open and 64 s to validate — a quadratic name-similarity check and dagre layout.)

## Database versions
Full backend suite, app storage *and* source on: MySQL 8.0.46 (local), 8.4.11, MariaDB 10.11 and 11.8. **MySQL 5.7 works as a source to read** (live tests) but Django 5 itself requires MySQL ≥ 8.0.11 / MariaDB ≥ 10.5 for the app's own storage.

## Still not tested / not done
Safari/WebKit (cannot run on this Windows machine) and Firefox/Edge (skipped at your request) · the three real product repos · the LLM against the real API · very large ontologies beyond 2,000 classes · real-world PDF/contract corpora · load testing with many simultaneous users · spaCy-style ML NER (blocked by a Windows application-control policy here) · PostgreSQL/Oracle/SQL Server (removed / not wanted) · ontology rendering of 2,000+ classes in *hierarchical* layout takes ~8 s by nature.
