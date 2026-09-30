# Prime Ontology — "ontology control room" redesign

Written for: engineers and designers continuing work on the shared workbench. Everything below was checked against the code in this repository (2026-09-30); anything not verified is listed under **Remaining gaps**.

## 1. Audit of the implementation (before the redesign)

### Roles — verified in `backend/prime_ontology/identity.py` and `frontend/src/workbench/context.jsx`
| Role | Capabilities (server-enforced) |
|---|---|
| viewer | `read` — graph, explorer, queries, validation, versions, audit. May *request* AI proposals and mapping proposals (read endpoints) but cannot apply/save them. |
| editor | + `write` (import, edit, map, save queries/mapping sets, apply AI proposals, edit agent registry), `commit`, `submit` |
| reviewer | + `review` (approve / reject). **Four-eyes:** the author of a version cannot approve or reject it (`versioning.transition`). |
| admin | + `publish`, `rollback`, `delete` |

A fifth server-side value, `none`, is returned for an unauthenticated host session (viewer-level UI, no data).

### Shell and tabs
Top bar (host title, ontology selector, New, status chips, Undo / Save / Export / Delete, user chip, Sign out) plus eight tabs: Import & Sources, Workbench, Mapping, Validation & Reasoning, Explore & Query, Versions & Governance, AI Assistant, Agents. Tabs are filtered by the host's `capabilities`; **Agents appears only in PrimeAgentic OS** (`embedded.py`).

### The six journeys today
1. **Sign in** — *Demo login* only **fills** `admin / admin123`; the user must then press **Sign in**. No ontology opens automatically (landing tab is Import & Sources) unless `?ontology=<id>` is in the URL.
2. **Import** — MySQL form or SQL script → *Inspect schema* (optional) → *Generate ontology* → candidate review (graph, mapping table, source structure, document sections/evidence) → *Approve & create* or *Merge into current*. Nothing is saved before approval. Files (CSV, Excel, JSON, XML, RDF, PDF, DOCX…) take the same review path.
3. **Edit** — three panes (Explorer 250 px · React Flow graph · Inspector 380 px); edits are local until Save (Ctrl+S); Undo; Export (Turtle, RDF/XML, JSON-LD, N-Triples).
4. **Map** — paste/upload source fields → *Propose mappings* → per-row accept / reject / override → *Save mapping set* → *Commit* writes lineage into the draft ontology.
5. **Validate & explore** — validation issues jump to the element; RDFS / OWL 2 RL reasoner; SHACL generate + validate; semantic search, concept card, path finder, natural-language and read-only SPARQL query.
6. **Govern** — commit version → submit → approve/reject (reviewer, not the author) → publish (admin); diff; rollback (admin); audit trail.

### Differences between the brief and the code
- *Demo login* does not sign in by itself (brief: "sign in using the Demo login button").
- The **Validation & Reasoning** tab has no provenance panel. Provenance is shown in the Inspector, the Concept card (Explore) and candidate-review evidence.
- The backend defines a `govern` capability (admin) that no screen uses.
- "Individuals" can be viewed and queried but there is no editor for them.
- The AI Assistant is rule-based unless an LLM is configured; the LLM path has never been exercised here (no API key).

### Suspected weaknesses — verdicts
| Hypothesis | Verdict |
|---|---|
| Looks like a generic admin dashboard | **Agree** — default MUI, uniform blue, no ontology cues. |
| Graph not central / polished | **Agree** — plain nodes, thin edges, no legend, default background. |
| Three panes cramped, unclear hierarchy | **Agree** — no pane titles; fixed 250/380 px panes at any width, no tablet/mobile behaviour. |
| Class / data property / relationship / inheritance hard to tell apart | **Agree** — one blue for everything; data properties were bare names. |
| Selected ontology / element / unsaved state hard to see | **Partly** — a "Save changes" button changed style, but there was no explicit unsaved indicator. |
| Import / validation / mapping / governance lack status and next steps | **Agree** for Import, Mapping and Governance; Validation already had a clear score. |
| Login → product not consistent | **Agree** — plain card on grey. |
| Poor small-screen / reduced-motion behaviour | **Agree** for small screens; the old UI had no animation to reduce. |
| *Added:* disabled controls never said why | Approve/Reject were enabled for the version's author and only failed after the click; role-locked buttons were silently greyed. |
| *Added:* drop-zone not keyboard reachable | It was a click-only `div`. |

## 2. Design system (implemented)

**Direction.** Dark graphite "control room" by default (light theme kept and switchable), one restrained accent per host, a fixed colour + icon + label per ontology concept, hairline grid and graph motifs used only where they carry meaning (login hero, graph canvas, empty states).

**Theme behaviour and compatibility.** `frontend/src/workbench/theme.js` exports `createOntologyTheme({ mode, accent })`. Light mode keeps the original `#1565c0` primary, so it is a drop-in for the previous theme. The standalone app defaults to dark and remembers the choice (`localStorage`, guarded). **Embedded hosts are unaffected:** they keep passing their own MUI `theme` (or none); every component reads colours through `useOnt()`, which uses `theme.palette.ontology` when present and otherwise derives the tokens from the host theme's palette *mode*, so the workbench is readable inside a light or dark host without adopting our theme. Per-host accent lives in `HOST_ACCENTS` (placeholder configuration, not brand assets).

**Concept tokens** (dark / light; each also has an icon and a text label, so colour is never the only signal):
| Concept | Colour | Icon | Graph treatment |
|---|---|---|---|
| Class | cyan `#3dd6f5` / `#00728f` | Hub | header-tinted card |
| Relationship (object property) | blue `#7aa9ff` / `#1565c0` | Share | solid arrow, mono label chip |
| Data property | green `#4fe3b0` / `#0a7a58` | Label | `name  datatype` rows inside the class |
| Inheritance | slate `#a3b3c9` / `#586a80` | SubdirectoryArrowRight | dashed, open arrow |
| Source / provenance | slate-blue | Storage / Fingerprint | chips |
| Mapping | pink `#f27fc0` / `#b0226b` | SyncAlt | — |
| AI suggestion | violet `#a996ff` / `#6a42d6` | AutoAwesome | dashed outline = "proposed, not applied" |
| Unsaved | amber | dot | top-bar chip |

**Files.** `theme.js` (tokens, theme), `components/ui.jsx` (KindChip, KindGlyph, StatusChip, PageHeader, Panel, EmptyState, RoleNotice, Why, DiffLines, StepTrack), `components/BrandMark.jsx`, `src/OntologyHero.jsx` (login graph).

## 3. Redesign prompt (for a design tool or implementation agent)

> Redesign **Prime Ontology Workbench** — a React 18 + MUI v5 + React Flow module embedded in UniContractAI, PrimeSemOnto and PrimeAgentic OS — as a futuristic but practical "ontology control room". Preserve every route, API call, auth flow and workflow; invent no features, roles, metrics or records. Use only concepts the product really has (e.g. the demo model Customer, SalesOrder, SalesOrderItem, Product, Supplier).
>
> **Visual language.** Deep graphite surfaces (`#070b11` / `#0c131b`), hairline borders, Inter + JetBrains Mono. Controlled accents: cyan for classes, blue for relationships, green for data properties, slate dashed for inheritance, pink for mapping, violet for AI proposals, amber only for unsaved. Every concept pairs colour with a fixed icon and text label. Subtle grid and graph motifs only; no glow washes, no decorative gradients, no flashing. Motion ≤ 200 ms and disabled under `prefers-reduced-motion`. A light theme with the original `#1565c0` primary must remain, and the whole UI must be token-driven so an embedding host's theme (light or dark) still renders legibly.
>
> **Login.** Full-height split. Left: a slowly rotating 3D ontology graph — luminous class nodes (the five demo concepts, labelled) joined by relationship edges, small green satellite nodes for data properties, faint depth points, a soft travelling pulse on relationships. It must read as a knowledge graph, not a brain/coin/orb; be decorative only (`aria-hidden`, `pointer-events:none`); load lazily so the form is interactive first; pause when hidden; render a single static frame under reduced motion; fall back to a static SVG of the same graph without WebGL. Right: a solid, high-contrast card with Username, Password (show/hide), Sign in (disabled until both filled), divider, **Demo login** (fills credentials only), inline error. On phones the card sits at the bottom over the dimmed graph.
>
> **Shared shell.** Brand mark + host title; a prominent ontology selector with **New**; status chip and "published vX" chip; an explicit *Unsaved changes* chip (amber dot) vs *All changes saved* / *Read-only*; Undo, **Save changes** (Ctrl+S), Export menu, Delete (admin only); a role chip that opens "what can my role do?" listing every capability, which are locked and which role unlocks them, plus the four-eyes rule; theme toggle (standalone) and Sign out. Below: icon + label tabs (Import & Sources, Workbench, Mapping, Validation & Reasoning, Explore & Query, Versions & Governance, AI Assistant, Agents — hosts hide the ones they do not enable). Buttons collapse to icons on small screens but keep accessible names.
>
> **Every tab** opens with an eyebrow, a one-line purpose and its main action, then one status/next-step cue.
> - *Import & Sources:* a 3-step track (Choose source → Review candidate → Approve or merge). Two panels: Database (MySQL | SQL script, Inspect schema optional, Generate) and Files & documents (keyboard-operable drop-zone, supported formats). The candidate review is a violet-topped panel ("nothing saved until you approve") with tabs Graph · Source → ontology mapping · Source structure · Document sections, evidence cards with page/section, Approve & create / Merge into "<name>" / Discard.
> - *Workbench:* three titled panes — Explorer (typed sections with counts: Classes tree, Object properties, Data properties, read-only Individuals), graph canvas, Inspector. The graph keeps every React Flow interaction (drag nodes, drag-to-connect in relationship or inheritance mode, layouts, search dimming, minimap) and adds class cards with typed data-property rows, mono edge labels, a legend and a first-fit after measurement. The Inspector is sectioned (Identity · Inheritance & logic · Provenance · Data properties · Relationships / Incoming). A relationship shows `Domain —name→ Range` with cardinality and a plain-language cardinality hint. Below 1200 px Explorer and Inspector become drawers and selecting opens the Inspector.
> - *Mapping:* Sources panel, saved sets panel, a "Next:" cue that changes with state, and a proposal table (source chip + field, target select, HIGH/MEDIUM/LOW chip, reasons, decision chip with accept/reject), Accept all HIGH, Save mapping set, Commit.
> - *Validation & Reasoning:* health ring with number and word, error/warning/pass counts (muted at zero), filterable findings with severity icon + text + code + "jump to element"; Reasoning (RDFS | OWL 2 RL) with consistency banner and inferred hierarchy/types; SHACL generate + validate.
> - *Explore & Query:* search hits with type glyphs; connection finder as a chip path; concept card (properties incl. inherited, relationships, sources & provenance, neighbour graph without toolbar); Query studio (words | SPARQL, examples, generated SPARQL, results, saved, history; read-only).
> - *Versions & Governance:* Draft → In review → Approved → Published track; commit panel; versions table showing only the valid next step, disabled *with a reason* (role, unsaved edits, or four-eyes when the user authored the version); Diff with + / − / ~ markers; audit trail; rollback confirmation.
> - *AI Assistant:* proposals in a dashed violet container labelled "Proposed — not applied", source chip (AI model / rule-based), typed operation list, diff, projected health, **Approve & apply** (editor+, saved state) / Reject.
> - *Agents (PrimeAgentic OS only):* Tools & workflows and Policies panels with effect chips, a semantic planner that states it does not execute, decision chip, concept chain with tool bindings, collapsible LLM context pack.
>
> **Responsive & accessible.** Desktop three-pane; tablet/mobile drawers, wrapped toolbars, no horizontal page scroll, minimap hidden on phones. Visible focus (2 px accent), labels on every control, contrast ≥ 4.5:1 for text, status never colour-only, honest empty/read-only/unavailable states with tooltips explaining every disabled control.

## 4. What changed
- New: `theme.js`, `components/ui.jsx`, `components/BrandMark.jsx`, `src/OntologyHero.jsx` (dependency-free WebGL, 8 kB lazy chunk).
- Rewritten UI (logic preserved): `LoginPage.jsx`, `main.jsx` (theme + colour mode), `PrimeOntologyWorkbench.jsx` (shell; public component now applies a host `theme` around an inner `Shell` so hooks see it), `OntologyGraph.jsx`, `Explorer.jsx`, `Inspector.jsx`, and all eight tabs.
- Small additions: `layout.js` exposes `propInfo` (datatype per property); `context.jsx` exposes `CAPABILITY_LABEL` / `requiredRole`; `index.js` exports `createOntologyTheme`, `HOST_ACCENTS`, `useOnt`; `index.html` loads Inter / JetBrains Mono (falls back to system fonts).
- Behaviour changes worth knowing: Approve/Reject are now **disabled** (with a visible note) for the version's author instead of failing after the click — the server still enforces it, and `e2e/smoke.mjs` now asserts both. The graph fits the view after nodes are measured (previously it could zoom to the maximum when positions were already saved). The drop-zone is keyboard-operable.

## 5. Verified
- `npm test` (vitest, 6 tests) and `vite build` pass.
- Browser E2E (`e2e/smoke.mjs`, Chrome, live backend): **all 16 steps pass** — run with the *MySQL connection step replaced by the SQL-script source* because no DB password was available this session. The MySQL-connection path itself was therefore not re-run after the redesign.
- Screenshot review, dark and light, of the login (desktop, phone, reduced motion, no-WebGL), every tab at 1500 px, the Workbench at 900 px (drawer) and 390 px, the Agents tab under the PrimeAgentic OS context, and the viewer role on Sources / Versions / Assistant.
- Login hero: WebGL mode, SVG fallback when `getContext('webgl')` returns null (DOM confirms `data-mode="fallback"`), and a pixel-identical static frame under `prefers-reduced-motion`; ~133 rAF/s in headless software GL with the hero running; form interaction unaffected (`pointer-events:none`).

## 6. Remaining gaps
- Not run: the MySQL-connection E2E step; the LLM assistant path (no API key); real host repositories (UniContractAI / PrimeSemOnto / PrimeAgentic OS — only their contexts in the standalone app); OCR.
- Not done: automated accessibility audit (axe) and a measured contrast pass — contrast was chosen by design, not measured; a real-device touch test; the graph does not yet group or swimlane classes and edge labels can still crowd on very large ontologies; no individuals editor; no activity/notification centre (toasts only); Inter / JetBrains Mono come from Google Fonts in standalone mode only.
- Per-host accents in `HOST_ACCENTS` are placeholders for the hosts' real branding tokens.
