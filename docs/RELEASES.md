# Status vs. the plan (PrimeOntology.txt)

Legend: ✅ built **and verified** · 🟡 built, limited, or verified only partly · ❌ not built.
Verification: **B** = backend automated tests (71) · **U** = frontend unit tests (6) · **E** = real-browser E2E (16 steps, Chrome, live API + MySQL; also run against the Docker stack) · **L** = live MySQL 8 · **H** = built and rendered inside a real Next.js 14 host.

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
❌ Visual *grouping* of nodes. ❌ Individuals editor (individuals can be stored, imported and queried, but not edited in the UI).

## R3 — Universal data → ontology ✅ B E
CSV, TSV, Excel (multi-sheet), JSON (nested → related tables), XML, YAML, SQL DDL, RDF/XML, OWL, Turtle, N-Triples, N-Quads, JSON-LD, N3, TriG. Type inference, PK guess, FK inference across files and sheets,
source→ontology mapping table, merge into an existing ontology (existing definitions win, provenance accumulates).
❌ Parquet · SQL Server / Oracle / SQLite connections · REST / GraphQL / SAP / Salesforce / ServiceNow / SharePoint / S3 / warehouse connectors (the plan marks these "eventually").

## R4 — Document → ontology 🟡 B E
PDF (text), DOCX, PPTX, TXT, Markdown, HTML → sections (page / heading) → concepts, relationships and data properties with **evidence: page + section + snippet** ("Found in …: Page 2, Section 'PAYMENT TERMS'").
Contract lexicon (Contract, Party, Customer, Supplier, Clause, Obligation, Payment, Term, Jurisdiction, Governing law, Liability, …), defined terms (`("Buyer")`), recurring capitalised phrases, date / amount / law extraction.
🟡 Extraction is **lexicon / pattern based, not ML NER**; quality outside contract-style language will be lower.
🟡 Tested with generated documents, not a corpus of real contracts.
🟡 Scanned PDFs are *detected* and reported ("OCR required"); **OCR is not performed**.

## R5 — AI semantic mapping ✅ (deterministic) B E · 🟡 (LLM)
Abbreviation expansion, synonym groups, token / sequence similarity, datatype compatibility, source↔concept context, explainable reasons, HIGH / MEDIUM / LOW confidence, top-K candidates,
duplicate-field detection, accept / reject / manual override, saved mapping sets, **commit lineage into the ontology only on explicit action**.
Verified with the plan's example: `cust_nm` / `customer_name` / `client_name` → `Customer.customerName`, `Buyer` → `Customer`.
AI assistant: proposal → diff → approve, never silent (B E); templates for contract / sales / agent domains; commands (add class, connect, add property, rename, delete).
🟡 **The LLM path is implemented (`llm.py`, Anthropic API) but was NOT tested against the live API** (no key available). Without `ANTHROPIC_API_KEY` everything falls back to rules.
❌ Semantic embeddings / vector similarity.

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
❌ Branching / merging of ontology branches.

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

## Not tested
LLM code path · Browsers other than Chrome · UI performance with very large ontologies (backend handled 178 classes / 2,100 properties; the graph UI was only exercised with ≤ 20 classes) ·
real-world PDF corpora and OCR · MySQL versions other than 8.0/8.4 · multi-user concurrent editing (last save wins) · load / performance testing.
