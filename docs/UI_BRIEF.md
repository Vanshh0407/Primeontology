# Prime Ontology Workbench — complete product & UI brief

> Purpose of this document: give a designer/LLM everything needed to write a UI redesign prompt. It describes what the product does, every screen, every control, every state, the data behind them, and the technical constraints. Everything below **exists and works today**.

---

## 1. What the product is
**Prime Ontology Workbench** is a web tool for building, governing and using an *enterprise ontology* — a formal map of business concepts (e.g. Customer, Sales Order, Contract, Obligation), their properties and the relationships between them.

Users can generate an ontology automatically from a **MySQL database**, from **files** (CSV, Excel, JSON, XML, RDF …) or from **documents** (PDF, Word, PowerPoint …), then edit it visually, map other data sources onto it, validate it, query it, version/approve/publish it, and let AI agents use it.

**It is one reusable module** embedded unchanged in three sister products:
| Host product | Context id | Focus |
|---|---|---|
| **UniContractAI** | `unicontractai` | Contract management: parties, clauses, obligations, payments, risks, regulations. Document import is the main entry point. |
| **PrimeSemOnto** | `primesemonto` | Central semantic-modelling platform. Shows every tab except Agents. |
| **PrimeAgentic OS** | `primeagenticos` | Semantic control plane for AI agents. The only host that shows the **Agents** tab. |

The host owns branding, navigation, login and theme; the workbench owns everything ontology-related. It also runs **standalone** (its own login page).

## 2. Users & roles
| Role | Can |
|---|---|
| **viewer** | Read everything (graph, explorer, queries, validation, versions, audit). No edit controls are shown. |
| **editor** | Import, edit ontology, map, commit versions, submit for review, save queries. |
| **reviewer** | + Approve / reject submitted versions (cannot approve own work — "four-eyes"). |
| **admin** | + Publish, roll back, delete ontologies. |

Personas: ontology/semantic engineer (power user), business analyst/data steward (imports & mapping), reviewer/governance officer (approve/publish), contract manager (document import), AI/agent builder (Agents tab).

## 3. Global shell (what's on every screen)
Single full-height page (`100vh`), light theme, MUI look.

**Top bar (one row + tabs row):**
- Host title (from host: e.g. "PrimeSemOnto · Enterprise Semantic Model").
- **Ontology selector** dropdown (each option: name + "N classes · status") and a **+ New** button (dialog asks for a name).
- Status chip (`draft | review | approved | published | archived`) and a "published vX.Y" chip.
- Right side: **Undo**, **Save changes** (highlighted only when there are unsaved edits; also Ctrl+S), **Export** menu (Turtle .ttl, RDF/XML·OWL, JSON-LD, N-Triples), **Delete** (admins only), a **user chip** ("admin · admin") and **Sign out** (standalone mode only).
- **Tab strip** (scrollable): Import & Sources · Workbench · Mapping · Validation & Reasoning · Explore & Query · Versions & Governance · AI Assistant · Agents (agentic host only). Tabs a host doesn't enable are hidden.

**Cross-cutting behaviours:** toast notifications bottom-left (success/info/error), unsaved-changes guard (confirm on switching ontology / signing out / closing tab), all edits are local until *Save*, viewers see read-only UI, empty states ("Select or create an ontology first"), error banner if the API is unreachable.

## 4. Login page (standalone app only)
Centered card on light-grey background: logo icon, "Prime Ontology Workbench", "Sign in to continue". Fields **Username**, **Password** (show/hide eye), primary **Sign in** button (disabled until both filled), divider "or", outlined **Demo login** button that **fills** the demo credentials (`admin` / `admin123`) without submitting, plus a hint line. Shows inline error ("Invalid username or password.", lockout message after 5 failures).

## 5. Screens (tabs) in detail

### 5.1 Import & Sources
Two cards side by side, then a *Candidate review* panel below.

**Card A – Database → Ontology**
- Sub-tabs **MySQL | SQL script**.
- MySQL form: Host, Port, User, Password, Database/schema, checkbox "Infer relationships from column names when the database declares no foreign keys".
- Buttons **Inspect schema** (shows *Schema preview*: each table with column chips — primary keys highlighted, "→ FK" markers — plus FK lines, "inferred from naming" tag) and **Generate ontology**.
- Note: "Credentials are used for this request only and are never stored."
- SQL script tab: large monospace textarea for `CREATE TABLE …`.

**Card B – Files & documents → Ontology**
- Large dashed **drag-and-drop / click-to-browse** zone. Supported: CSV, TSV, Excel, JSON, XML, YAML, SQL, RDF/OWL, Turtle, JSON-LD, N-Triples, PDF, DOCX, PPTX, TXT, Markdown, HTML. Spinner while analysing.

**Candidate review panel** (appears after Generate / upload; *nothing is saved yet*):
- Header with counts chips (classes / properties / relationships) and source-format chip; warning alerts (e.g. "PDF has pages with no extractable text").
- Sub-tabs: **Graph** (interactive read-only graph; click a class → side panel), **Source → ontology mapping** (table: source, column, ontology element, kind/datatype), **Source structure** (schema preview), **Document sections** (list of detected sections with page + heading + snippet; documents only).
- For documents, selecting a class shows **evidence cards**: "Found in contract.pdf: Page 17, Section '8.2 Payment' — “…snippet…”" and a confidence chip.
- Footer: name field, **Approve & create ontology**, **Merge into "<current ontology>"** (combines with the ontology already open), **Discard**. Caption: "Nothing is saved until you approve."

### 5.2 Workbench (the main editor) — 3-pane layout
**Left pane (250 px) – Ontology Explorer:** "+ Class" button, filter box, collapsible sections: *Classes* (hierarchical tree by inheritance), *Object properties* (name + "Domain → Range"), *Data properties* (name + "Domain · datatype"), *Individuals*. Click selects and highlights on canvas.

**Center – Graph canvas (React Flow):**
- Toolbar (own row): search box, layout selector (**Hierarchical top-down / left-right / Circular / Grid**), **Layout** button, toggle **Relationships / Inheritance** visibility, **Properties** switch (show property lists inside nodes), connect-mode toggle (**Drag = relationship / Drag = inheritance**), badge "N classes · M links".
- Class node = rounded card: blue header with class name, body listing first 4 data properties (+N more). Selected = thick blue border; search match = orange border; non-matching dimmed.
- Edges: solid blue arrows labelled with relationship name; inheritance = dashed grey hollow arrow labelled "subClassOf".
- Zoom/pan, controls, **minimap**, dotted background, drag nodes (positions saved with the ontology). Drag from one node's handle to another → dialog "New relationship A → B" (name prefilled `hasB`) or instantly creates inheritance (blocked with message if it would create a cycle).

**Right pane (380 px) – Inspector**
- *Class editor:* title + **Explore** / **Delete** buttons; fields: Name (IRI), Label, Description, Parent classes (multi-select), Equivalent classes, Disjoint classes, Synonyms (free-text chips); provenance chips (database/table/document …); **Data properties table** (name, datatype dropdown, Required checkbox, Min, Max, delete) + "New property / type / Add" row; **Relationships from this class** (clickable chips with cardinality) + "New relationship / Range / Add"; **Incoming** relationships chips.
- *Relationship editor:* Name, Label, Description, Domain (from), Range (to), Cardinality (many-to-one / one-to-many / many-to-many / one-to-one), Min, Max, Required, Inverse-of, source chip ("from sales_order.customer_id (inferred)"), **Delete relationship**.
- Empty state text when nothing selected. Renaming a relationship (e.g. `places` → `createsOrder`) updates the graph label instantly.
- Validation errors appear as toasts (e.g. "Use letters, digits, _ or - and start with a letter", "would create a cycle").

### 5.3 Mapping (AI Semantic Mapping Studio)
- Intro + textarea "Sources — one per line: `Name: field, field`" (default example CRM/ERP/Contract), buttons **Propose mappings** and **From files (CSV, Excel, JSON, SQL…)**; progress bar.
- Right card: **Saved mapping sets** (chip per set with ✓ count, **Commit**, delete ×).
- Info alerts for **duplicates** ("Customer.customerName ← CRM.customer_name, ERP.cust_nm — likely the same attribute").
- **Proposal table** columns: *Source field* (source.field + type) · *Proposed ontology target* (dropdown: ranked candidates with %, then any class/property, or "(unmapped)") · *Confidence* chip (HIGH green / MEDIUM amber / LOW grey, with %; "manual" for overrides) · *Why* (e.g. "normalized-name match; compatible datatype") · *Decision* chip (proposed / accepted / rejected / overridden / unmapped) + ✓ Accept and ✗ Reject buttons. Rejected rows fade.
- Footer: **Accept all HIGH**, mapping-set name field, **Save mapping set (N accepted)**. **Commit** writes accepted mappings as lineage into the ontology (explicit action; the AI never changes the ontology silently).

### 5.4 Validation & Reasoning — 3 sub-tabs
1. **Validation:** big circular **health score** (0–100, green/amber/red) + chips *errors / warnings / passed* + counts (classes, properties, relationships); **Re-validate**; filter toggle *All / Errors / Warnings*; issue list rows = severity chip + message + affected elements + code chip (e.g. `DUPLICATE_CLASS`, `ORPHAN_CLASS`, `CIRCULAR_HIERARCHY`, `INVALID_DOMAIN`, `MISSING_RANGE`, `CARDINALITY_CONFLICT`, `INCOMPATIBLE_RANGE`, `MAPPING_CONFLICT` …). Clicking an issue **jumps to the element in the Workbench**.
2. **Reasoning (RDFS / OWL):** profile toggle **RDFS | OWL 2 RL**, **Run reasoner**; result: consistency banner (green "consistent" / red with reasons) + triple counts before→after; card "Inferred class hierarchy" (`PremiumCustomer ⊑ Customer via IndividualCustomer`); card "Inferred individual types".
3. **SHACL:** **Generate SHACL shapes** (read-only Turtle textarea), textarea to paste a data graph (Turtle) (empty = validate own individuals), **Validate against shapes**, result alert (conforms / list of violations with node, path, message).
- Banner if there are unsaved edits ("validation runs on last saved version").

### 5.5 Explore & Query — 2 sub-tabs
**Knowledge explorer** (left 1/3): semantic search box (fuzzy, synonyms; results list with kind, domain, score %); **"How are two concepts connected?"** — From/To autocompletes → shows path "Customer —hasOrder→ SalesOrder …" or "Not connected".
Right 2/3: **Concept card** — title + "Open in workbench"; chips "is a X" / "Y is a kind of it" (clickable navigation); a small **neighbour graph**; three columns *Properties* (incl. inherited, with datatype), *Relationships* (outgoing filled chips, incoming outlined chips, all clickable), *Sources & provenance* (DB table / document + page + section / mapped source fields). Empty state hint.

**Query studio:** toggle **Ask in words | SPARQL**; multiline input (monospace for SPARQL); **Run**, **Save query**, example chips (All classes, Relationships, Subclasses, Required properties). For natural language: an *Interpretation / Path* banner ("Customer → SalesOrder → Product → Supplier"), the *generated SPARQL* box, results table (rows clickable into concepts, "N rows, truncated"), note when the ontology has no instance data. Right column: **Saved queries** (delete) and **History** (kind + row count). Write/update SPARQL is blocked with a clear error.

### 5.6 Versions & Governance — 3 sub-tabs
- Top: **Lifecycle** chips (working copy status, published version, "unsaved edits" warning); commit card: message field, minor/major dropdown, **Commit version** (disabled while unsaved edits).
- **Versions table:** version, status chip (draft/review/approved/published/archived), message, author/reviewer, size, actions **Submit for review / Approve / Reject / Publish** (only the valid next step for your role), **Diff**, **Export**, **Rollback** (confirmation dialog).
- **Diff:** From/To selectors (versions or "Working copy"), **Compare**; monospace list coloured green `+ Class: Supplier`, red `- Class: LegacyCustomer`, orange `~ Customer → Client`, property changes.
- **Audit trail:** table when / who / action / detail for every change (created, generated, model.update, version.commit/submit/approve/publish/rollback, mapping.committed, ai.applied, exported …).
- Workflow: Draft → Review → Approved → Published; editing a published ontology reopens it as draft; author cannot approve own version.

### 5.7 AI Assistant
Prompt input + **Propose**; suggestion chips ("Create an ontology for contract management", "Connect Contract to Customer", "Add class Vehicle under Asset", "Add property price to Product as decimal", "Rename Customer to Client"). Result **Proposal card**: source chip (AI model / rule-based), notes, list of operations ("Add class Obligation", "Add relationship Contract —hasParty→ Party"), a coloured **diff**, "after applying: health 96/100 · 0 errors", buttons **Approve & apply** / **Reject**. Never changes anything until approved.

### 5.8 Agents (PrimeAgentic OS only)
Two columns: **Tools & workflows** (each: name, type api/mcp/workflow/function, "operates on concepts" multi-select, operations read/create/update/delete/approve/execute, endpoint/MCP server id; + Tool, delete) and **Policies & business rules** (name, effect allow / require_approval / deny, concepts, operations, description; + Policy). **Save registry**.
Below: **Semantic planner** — request input + **Plan**. Result: decision chip (ready / partial / needs approval / blocked / unreachable / clarify), concept chain "Contract → Supplier → Product", policy alerts, "no tool bound for …" info, numbered steps ("1. read Customer → [api: crm_lookup]"), and a collapsible **LLM context pack** preview. (A read-only MCP server exposes the same to agents.)

## 6. Current visual design (baseline)
- Material UI v5, default Roboto, 8 px radius. Primary blue `#1565c0`; page background `#f5f7fa`; white paper surfaces with 1 px dividers; light theme only.
- Graph: blue node headers (#4a90d9 family), blue solid relationship edges, grey dashed inheritance edges.
- Status colours: success green / warning amber / error red / info blue / neutral grey (chips everywhere).
- Density: compact (small inputs/buttons). Tabs uppercase (MUI default). Monospace for code/SPARQL/diff.
- The host application's MUI theme is inherited automatically when embedded (so the design must be **theme-token driven**, not hard-coded).

## 7. Data the UI works with (so mock screens are realistic)
- **Ontology:** `{id, name, status, currentVersion, stats{classes, dataProperties, objectProperties}, model}`.
- **Class:** name, label, comment, parents[], equivalentTo[], disjointWith[], synonyms[], source{database, table | document}, evidence[{document, page, section, snippet}], confidence.
- **Data property:** name, domain, datatype (string, integer, decimal, boolean, date, dateTime …), required, identifier, min/maxCardinality, source{table, column}.
- **Relationship:** name, domain, range, cardinality, required, inverseOf, source (incl. `inferred`).
- **Validation issue:** severity, code, message, targets[]. **Version:** number, status, message, createdBy, reviewedBy, stats. **Audit event:** action, actor, detail, time.
- **Mapping row:** source, field, type, candidates[{target, score, confidence, reasons}], status.
- **Demo content:** Customer / Product / Supplier / SalesOrder / SalesOrderItem (from a MySQL demo database); a sample contract (Contract, Party, Supplier, Customer, Payment, Obligation, Termination, GoverningLaw …).

## 8. Technical constraints for any redesign
- React 18 + **MUI v5** (must stay compatible: hosts are Next.js apps using the "Material UI Free Template"); **React Flow (@xyflow/react)** for the graph; dagre for layouts. No other UI framework.
- Delivered as one component `<PrimeOntologyWorkbench apiBase ontologyId context permissions getAuthHeaders onEvent theme height />`; must render inside a host page below the host's app bar (height configurable) and inherit its theme; must also work full-page standalone.
- Responsive expectations: desktop-first (≥1280 px, three-pane workbench), should degrade gracefully to tablet (collapse explorer/inspector into drawers); mobile is read-only nice-to-have.
- Accessibility: keyboard reachable controls, visible focus, colour is never the only status signal (chips have text), sufficient contrast; labels on all inputs.
- Must handle: empty states, loading, errors (toasts + inline), read-only (viewer) mode, long names, large ontologies (100+ classes; minimap, search, filter, collapse), unsaved-changes state.
- Ports/stack (informational): backend Django API :8008, standalone frontend :3008.

## 9. Known UI weaknesses worth redesigning
- Dense, utilitarian layout; many tabs with long forms; no dashboard/home overview (landing = Import tab).
- No dark mode; no responsive drawers for the workbench panes; no onboarding/guided flow for the "connect → generate → review → publish" journey.
- Graph nodes are basic; no grouping/swimlanes; edge label clutter on large graphs; no per-class colour/icon coding.
- Individuals (instance data) can be viewed/queried but have no editor.
- Notifications are toasts only; no activity/notification centre.
- Login page is minimal (no branding, no SSO buttons).

## 10. Suggested end-to-end user journeys to design around
1. **Database → ontology:** sign in → connect MySQL → inspect → generate → review graph → approve → open in Workbench → tweak a relationship name → save → export Turtle.
2. **Contract document → ontology (UniContractAI):** upload PDF → review concepts with page/section evidence → merge into the contract ontology → validate → commit → submit for review.
3. **Map a new source:** paste/upload CRM & ERP fields → review AI proposals with confidence → accept HIGH, override one → save set → commit lineage.
4. **Govern:** reviewer opens Versions → diff v1.0→v1.1 → approve → admin publishes → audit shows trail; later rollback.
5. **Ask a question:** "customers who placed orders for products supplied by suppliers" → see path + SPARQL + results → click into a concept.
6. **Agent setup (PrimeAgentic OS):** register tools, add a "require approval on delete" policy → plan a request → see bound tools and policy decision.
