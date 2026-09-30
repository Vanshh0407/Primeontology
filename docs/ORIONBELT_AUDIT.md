# OrionBelt audit (R1 task from the plan)

Scope: repository metadata, licence text, file trees and READMEs of `ralforion/orionbelt-analytics` and
`ralforion/orionbelt-ontology-builder`, fetched 2026-09-30. **This was not a line-by-line code review.**

## Critical finding: licence
Both repositories are under **Business Source License 1.1** (licensor RALFORION d.o.o.). The Additional Use Grant allows any use,
including production, **except**: *"you may not embed OrionBelt in a commercial product or provide it as part of a commercial service
without purchasing a commercial license"* (contact licensing@ralforion.com). Change date 2030-03-16 (analytics) / 2030-03-30 (builder),
after which the code becomes Apache-2.0.

UniContractAI, PrimeSemOnto and PrimeAgentic OS are commercial products, so **embedding OrionBelt code or running it as part of them
requires a commercial licence** (or legal sign-off). **No OrionBelt code was copied into this project.** Everything here is an independent
implementation on rdflib/owlrl/pyshacl/React Flow. Recommendation: have legal review; if you want OrionBelt itself, buy the licence — nothing
in this codebase depends on it either way.

## What they are
| | orionbelt-analytics | orionbelt-ontology-builder |
|---|---|---|
| Purpose | MCP server: analyses DB schemas → RDF/OWL ontologies + SQL mappings (R2RML) for fan-trap-free Text-to-SQL | Browser ontology workbench for OWL/SKOS |
| Backend | Python, FastMCP, SQLAlchemy, rdflib, Oxigraph, ChromaDB (GraphRAG) | Python, rdflib, OWL-RL |
| Frontend | none (MCP tools + a chart viewer) | Streamlit + vis-network |
| Sources | PostgreSQL, MySQL, Snowflake, ClickHouse, Dremio, BigQuery, DuckDB, Databricks | OWL/RDF/Turtle files, SKOS |
| Notable | SHACL validator, OBQC query checks, R2RML generator | 22 SKOS checks, SPARQL console, bulk ops, gist starters |

## Fit with the requirements
- Frontends are Streamlit / MCP — **not** embeddable React/MUI components; a wholesale fork would violate the "same React module" goal.
- Ontology model: rdflib graphs. Ours: a host-neutral JSON model (classes, properties, provenance, versions) that serialises to OWL/RDF.
- Extraction seams (schema reflection, RDF generation) are re-implemented here (`introspect.py`, `generator.py`) — same idea, own code.

## Not adopted (possible future, licence permitting)
R2RML mapping generation, GraphRAG retrieval over the ontology, SKOS vocabulary support, OBQC-style query validation.
