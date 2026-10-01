"""Schema dict -> ontology model dict -> RDF/OWL."""
from rdflib import OWL, RDF, RDFS, SKOS, XSD, BNode, Graph, Literal, Namespace, URIRef

from .naming import relationship_name, to_class_name, to_label, to_property_name
from .typemap import sql_to_xsd

DEFAULT_BASE_IRI = "http://primeontology.ai/onto#"


def _is_junction(table: dict) -> bool:
    fks = table["foreign_keys"]
    if len(fks) < 2:
        return False
    fk_cols = {c for fk in fks for c in fk["columns"]}
    return all(c["name"] in fk_cols for c in table["columns"])


def _unique(name: str, taken: set) -> str:
    candidate, i = name, 2
    while candidate in taken:
        candidate, i = f"{name}{i}", i + 1
    taken.add(candidate)
    return candidate


def generate_model(schema: dict) -> dict:
    """Mechanical + heuristic semantic mapping.
    table -> Class, column -> DatatypeProperty, FK -> ObjectProperty,
    pure junction table -> many-to-many ObjectProperty. Provenance retained."""
    src = schema["source"]
    tables = {t["name"]: t for t in schema["tables"]}
    class_names, class_taken = {}, set()
    for t in schema["tables"]:
        if not _is_junction(t):
            class_names[t["name"]] = _unique(to_class_name(t["name"]), class_taken)

    def prov(table, column=None, **extra):
        p = {"database": src.get("database"), "schema": src.get("schema"), "table": table}
        if column:
            p["column"] = column
        p.update(extra)
        return p

    classes, dprops, oprops = [], [], []
    for tname, cname in class_names.items():
        t = tables[tname]
        classes.append({"name": cname, "label": to_label(cname), "comment": f"Generated from table {tname}.",
                        "parents": [], "source": prov(tname)})
        fk_cols = {c for fk in t["foreign_keys"] for c in fk["columns"]}
        taken = set()
        for col in t["columns"]:
            if col["name"] in fk_cols:
                continue
            dprops.append({
                "name": _unique(to_property_name(col["name"]), taken), "label": to_label(to_property_name(col["name"])),
                "domain": cname, "datatype": sql_to_xsd(col["type"]),
                "required": bool(col.get("pk") or not col.get("nullable", True)),
                "identifier": bool(col.get("pk")),
                "source": prov(tname, col["name"], sqlType=col["type"]),
            })
        for fk in t["foreign_keys"]:
            target = class_names.get(fk["ref_table"])
            if not target:
                continue
            nullable = all(next((c["nullable"] for c in t["columns"] if c["name"] == x), True) for x in fk["columns"])
            oprops.append({
                "name": _unique(relationship_name(fk["columns"], target), taken),
                "label": to_label(relationship_name(fk["columns"], target)),
                "domain": cname, "range": target,
                "cardinality": "many-to-one", "required": not nullable,
                "source": prov(tname, ",".join(fk["columns"]), references=f'{fk["ref_table"]}({",".join(fk["ref_columns"])})',
                               inferred=bool(fk.get("inferred"))),
            })

    for t in schema["tables"]:
        if not _is_junction(t):
            continue
        fks = [fk for fk in t["foreign_keys"] if fk["ref_table"] in class_names]
        for i, a in enumerate(fks):
            for b in fks[i + 1:]:
                da, db = class_names[a["ref_table"]], class_names[b["ref_table"]]
                oprops.append({
                    "name": _unique(relationship_name(b["columns"], db), set(p["name"] for p in oprops if p["domain"] == da)),
                    "label": to_label(relationship_name(b["columns"], db)),
                    "domain": da, "range": db, "cardinality": "many-to-many", "required": False,
                    "source": prov(t["name"], None, viaJunction=t["name"]),
                })
    return {"classes": classes, "dataProperties": dprops, "objectProperties": oprops}


def stats(model: dict) -> dict:
    return {"classes": len(model["classes"]), "dataProperties": len(model["dataProperties"]),
            "objectProperties": len(model["objectProperties"])}


def prop_local(model: dict, p: dict) -> str:
    """Local IRI name of a property; qualified with its domain when the bare
    name is used by several properties."""
    same = [q for q in model["dataProperties"] + model["objectProperties"] if q["name"] == p["name"]]
    return f'{p["domain"]}.{p["name"]}' if len(same) > 1 else p["name"]


def model_to_graph(model: dict, name: str = "Ontology", base_iri: str = DEFAULT_BASE_IRI) -> Graph:
    ns = Namespace(base_iri)
    g = Graph()
    g.bind("onto", ns)
    g.bind("owl", OWL)
    g.bind("rdfs", RDFS)
    g.bind("xsd", XSD)
    g.bind("skos", SKOS)
    onto_iri = URIRef(base_iri.rstrip("#/"))
    g.add((onto_iri, RDF.type, OWL.Ontology))
    g.add((onto_iri, RDFS.label, Literal(name)))
    for c in model["classes"]:
        u = ns[c["name"]]
        g.add((u, RDF.type, OWL.Class))
        g.add((u, RDFS.label, Literal(c.get("label") or c["name"])))
        if c.get("comment"):
            g.add((u, RDFS.comment, Literal(c["comment"])))
        for p in c.get("parents", []):
            g.add((u, RDFS.subClassOf, ns[p]))
        for e in c.get("equivalentTo", []):
            g.add((u, OWL.equivalentClass, ns[e]))
        for d in c.get("disjointWith", []):
            g.add((u, OWL.disjointWith, ns[d]))
        for syn in c.get("synonyms", []):
            g.add((u, SKOS.altLabel, Literal(syn)))

    def restriction(cls, prop_iri, kind, n):
        r = BNode()
        g.add((r, RDF.type, OWL.Restriction))
        g.add((r, OWL.onProperty, prop_iri))
        g.add((r, kind, Literal(int(n), datatype=XSD.nonNegativeInteger)))
        g.add((ns[cls], RDFS.subClassOf, r))

    def common(p, u, kind_type):
        g.add((u, RDF.type, kind_type))
        g.add((u, RDFS.label, Literal(p.get("label") or p["name"])))
        if p.get("comment"):
            g.add((u, RDFS.comment, Literal(p["comment"])))
        g.add((u, RDFS.domain, ns[p["domain"]]))
        mn = p.get("minCardinality", 1 if p.get("required") else None)
        mx = p.get("maxCardinality", 1 if p.get("cardinality") == "many-to-one" or p.get("identifier") else None)
        if mn not in (None, "", 0):
            restriction(p["domain"], u, OWL.minCardinality, mn)
        if mx not in (None, ""):
            restriction(p["domain"], u, OWL.maxCardinality, mx)
        for sp in p.get("subPropertyOf", []):
            g.add((u, RDFS.subPropertyOf, ns[sp]))

    for p in model["dataProperties"]:
        u = ns[prop_local(model, p)]
        common(p, u, OWL.DatatypeProperty)
        g.add((u, RDFS.range, XSD[p["datatype"]]))
        if p.get("identifier"):
            g.add((u, RDF.type, OWL.FunctionalProperty))
    for p in model["objectProperties"]:
        u = ns[prop_local(model, p)]
        common(p, u, OWL.ObjectProperty)
        g.add((u, RDFS.range, ns[p["range"]]))
        if p.get("cardinality") == "many-to-one":
            g.add((u, RDF.type, OWL.FunctionalProperty))
        if p.get("inverseOf"):
            g.add((u, OWL.inverseOf, ns[p["inverseOf"]]))
    props = {(p["domain"], p["name"]): p for p in model["dataProperties"] + model["objectProperties"]}
    for ind in model.get("individuals", []):
        u = ns[ind["name"]]
        g.add((u, RDF.type, OWL.NamedIndividual))
        g.add((u, RDF.type, ns[ind["class"]]))
        g.add((u, RDFS.label, Literal(ind.get("label") or ind["name"])))
        for k, v in (ind.get("data") or {}).items():
            pr = next((q for q in model["dataProperties"] if q["name"] == k and q["domain"] == ind["class"]), None)
            if pr:
                g.add((u, ns[prop_local(model, pr)], Literal(v) if pr["datatype"] == "string" else Literal(v, datatype=XSD[pr["datatype"]])))
        for k, v in (ind.get("links") or {}).items():
            pr = next((q for q in model["objectProperties"] if q["name"] == k and q["domain"] == ind["class"]), None)
            for target in (v if isinstance(v, list) else [v]):
                if pr:
                    g.add((u, ns[prop_local(model, pr)], ns[target]))
    return g


FORMATS = {"turtle": ("turtle", "text/turtle", "ttl"), "xml": ("xml", "application/rdf+xml", "owl"),
           "json-ld": ("json-ld", "application/ld+json", "jsonld"), "nt": ("nt", "application/n-triples", "nt")}


def serialize(model: dict, fmt: str = "turtle", name: str = "Ontology", base_iri: str = DEFAULT_BASE_IRI):
    rdf_fmt, mime, ext = FORMATS[fmt]
    data = model_to_graph(model, name, base_iri).serialize(format=rdf_fmt)
    return data, mime, ext
