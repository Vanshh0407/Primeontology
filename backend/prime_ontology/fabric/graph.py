"""Knowledge-graph materialisation: golden entities + relations + provenance as RDF (SPARQL-queryable).

The graph is cached per (ontology, FabricState.version, ontology model revision) so repeated queries are cheap and any
sync / merge / decision invalidates it automatically.
"""
import re
import threading

from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef

from .. import generator, query
from .models import FabricEntity, FabricRecord, FabricRef, FabricState, property_provenance

PROV = Namespace("http://www.w3.org/ns/prov#")
FAB = Namespace("https://primeontology.io/fabric#")
_CACHE: dict[int, tuple[tuple, Graph]] = {}
_LOCK = threading.Lock()
MAX_ENTITIES = 200_000


def _local(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))


def entity_iri(ns: Namespace, e) -> URIRef:
    return ns[f"{_local(e.class_name)}_{e.id}"]


def _version(ontology) -> tuple:
    st = FabricState.objects.filter(ontology=ontology).first()
    return (st.version if st else 0, ontology.model_revision)


def build(ontology) -> Graph:
    model, base = ontology.model, ontology.base_iri
    g = generator.model_to_graph(model, ontology.name, base)
    ns = Namespace(base)
    g.bind("prov", PROV)
    g.bind("fabric", FAB)
    dprops = {(p["domain"], p["name"]): p for p in model.get("dataProperties", [])}
    by_name = {}
    for p in model.get("dataProperties", []):
        by_name.setdefault(p["name"], []).append(p)
    oprops = {}
    for p in model.get("objectProperties", []):
        oprops.setdefault(p["name"], []).append(p)
    ents = FabricEntity.objects.filter(ontology=ontology, status="active")[:MAX_ENTITIES]
    ids = set()
    for e in ents:
        ids.add(e.id)
        s = entity_iri(ns, e)
        g.add((s, RDF.type, ns[e.class_name]))
        g.add((s, RDFS.label, Literal(e.display_name or str(e.id))))
        for k, v in (e.canonical or {}).items():
            if v is None:
                continue
            p = dprops.get((e.class_name, k)) or (by_name.get(k) or [None])[0]
            pred = ns[generator.prop_local(model, p) if p else k]
            dt = {"integer": XSD.integer, "decimal": XSD.decimal, "boolean": XSD.boolean, "date": XSD.date, "dateTime": XSD.dateTime}.get((p or {}).get("datatype"))
            g.add((s, pred, Literal(v, datatype=dt) if dt else Literal(v)))
        for k, info in property_provenance(e).items():
            node = FAB[f"prov_{e.id}_{_local(k)}"]
            g.add((s, PROV.wasDerivedFrom, node))
            g.add((node, FAB.property, Literal(k)))
            g.add((node, FAB.source, Literal(info.get("source", ""))))
            g.add((node, FAB.externalId, Literal(str(info.get("external_id", "")))))
    # relationships between entities (record -> record resolved to entity -> entity)
    refs = FabricRef.objects.filter(ontology=ontology, to_record__isnull=False, from_record__entity__isnull=False, to_record__entity__isnull=False,
                                    from_record__deleted=False, to_record__deleted=False).values_list(
        "property", "from_record__entity_id", "to_record__entity_id", "from_record__class_name", "target_class")
    seen = set()
    for prop, a, b, acls, bcls in refs.iterator():
        if a not in ids or b not in ids or (prop, a, b) in seen:
            continue
        seen.add((prop, a, b))
        cands = [p for p in oprops.get(prop, []) if p.get("domain") == acls] or oprops.get(prop, [])
        pred = ns[generator.prop_local(model, cands[0]) if cands else prop]
        g.add((ns[f"{_local(acls)}_{a}"], pred, ns[f"{_local(bcls)}_{b}"]))
    # which systems hold the entity (data lineage in the graph)
    for rid, eid, cls, src, ext in FabricRecord.objects.filter(ontology=ontology, deleted=False, entity__status="active").values_list(
            "id", "entity_id", "entity__class_name", "source__name", "external_id").iterator():
        if eid in ids:
            node = FAB[f"record_{rid}"]
            g.add((node, RDF.type, FAB.SourceRecord))
            g.add((node, FAB.source, Literal(src)))
            g.add((node, FAB.externalId, Literal(ext)))
            g.add((ns[f"{_local(cls)}_{eid}"], FAB.hasRecord, node))
    return g


def get_graph(ontology) -> Graph:
    key = _version(ontology)
    with _LOCK:
        hit = _CACHE.get(ontology.id)
        if hit and hit[0] == key:
            return hit[1]
    g = build(ontology)
    with _LOCK:
        _CACHE[ontology.id] = (key, g)
        if len(_CACHE) > 16:
            _CACHE.pop(next(iter(_CACHE)))
    return g


def sparql(ontology, text: str) -> dict:
    return query.run_sparql(ontology.model, text, ontology.base_iri, ontology.name, graph=get_graph(ontology))


def neighbours(ontology, entity: FabricEntity) -> dict:
    """Incoming/outgoing relations of one entity, resolved to entities."""
    out, inc = [], []
    for r in FabricRef.objects.filter(from_record__entity=entity, to_record__isnull=False, to_record__entity__isnull=False, to_record__entity__status="active").select_related("to_record__entity")[:500]:
        t = r.to_record.entity
        out.append({"property": r.property, "entityId": t.id, "class": t.class_name, "name": t.display_name})
    for r in FabricRef.objects.filter(to_record__entity=entity, from_record__entity__isnull=False, from_record__entity__status="active").select_related("from_record__entity")[:500]:
        f = r.from_record.entity
        inc.append({"property": r.property, "entityId": f.id, "class": f.class_name, "name": f.display_name})
    dedup = lambda xs: list({(x["property"], x["entityId"]): x for x in xs}.values())  # noqa: E731
    return {"outgoing": dedup(out), "incoming": dedup(inc)}


def provenance_graph(entity: FabricEntity) -> dict:
    """Nodes/edges: entity <- source records <- systems, with which property each system supplied."""
    nodes = [{"id": f"e{entity.id}", "type": "entity", "label": entity.display_name or f"#{entity.id}", "class": entity.class_name}]
    edges, seen = [], set()
    for r in entity.records.select_related("source").filter(deleted=False):
        sid = f"s{r.source_id}"
        if sid not in seen:
            seen.add(sid)
            nodes.append({"id": sid, "type": "source", "label": r.source.name, "kind": r.source.kind})
        nodes.append({"id": f"r{r.id}", "type": "record", "label": r.external_id})
        edges.append({"from": sid, "to": f"r{r.id}", "label": "holds"})
        edges.append({"from": f"r{r.id}", "to": f"e{entity.id}", "label": "resolved to", "reason": r.link_reason})
    supplied = {}
    for prop, info in property_provenance(entity).items():
        supplied.setdefault(info.get("source", ""), []).append(prop)
    return {"nodes": nodes, "edges": edges, "suppliedBy": supplied}


def _cy_str(v) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", " ") + '"'


def _cy_val(v) -> str | None:
    if v is None or isinstance(v, (dict, list)):
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return _cy_str(v)


def to_cypher(ontology, limit: int = 50000) -> str:
    """Neo4j / openCypher script: one node per active entity (label = class) and one relationship per resolved reference.
    Re-runnable: nodes are MERGEd on a stable id. Property keys are validated identifiers; values are escaped literals."""
    import re

    ok = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    lines = ["// Prime Ontology knowledge fabric export (openCypher)"]
    ids = set()
    for e in FabricEntity.objects.filter(ontology=ontology, status="active")[:limit]:
        if not ok.match(e.class_name):
            continue
        ids.add(e.id)
        props = {"primeId": e.id, "name": e.display_name, **{k: v for k, v in (e.canonical or {}).items() if ok.match(k)}}
        body = ", ".join(f"{k}: {c}" for k, v in props.items() if (c := _cy_val(v)) is not None)
        lines.append(f"MERGE (n:{e.class_name} {{primeId: {e.id}}}) SET n += {{{body}}};")
    seen = set()
    for prop, a, b in FabricRef.objects.filter(ontology=ontology, to_record__isnull=False, from_record__entity__isnull=False, to_record__entity__isnull=False,
                                               from_record__deleted=False, to_record__deleted=False).values_list(
            "property", "from_record__entity_id", "to_record__entity_id").iterator():
        if a in ids and b in ids and (prop, a, b) not in seen and ok.match(prop):
            seen.add((prop, a, b))
            rel = re.sub(r"(?<!^)(?=[A-Z])", "_", prop).upper()
            lines.append(f"MATCH (a {{primeId: {a}}}), (b {{primeId: {b}}}) MERGE (a)-[:{rel}]->(b);")
    return "\n".join(lines) + "\n"
