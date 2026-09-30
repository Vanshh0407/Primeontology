"""RDF / OWL / Turtle / JSON-LD / N-Triples -> ontology model (no lossy table step)."""
from rdflib import OWL, RDF, RDFS, XSD, Graph, Literal, URIRef

from ..naming import to_label
from .common import IngestError
from .tabular import _decode

RDF_FORMATS = {".ttl": "turtle", ".turtle": "turtle", ".rdf": "xml", ".owl": "xml", ".xml": "xml",
               ".nt": "nt", ".nq": "nquads", ".jsonld": "json-ld", ".n3": "n3", ".trig": "trig"}


def local(u) -> str:
    s = str(u)
    for sep in ("#", "/"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s or str(u)


def parse_graph(filename: str, data: bytes, fmt: str | None = None) -> Graph:
    import os

    fmt = fmt or RDF_FORMATS.get(os.path.splitext(filename)[1].lower(), "turtle")
    g = Graph()
    try:
        g.parse(data=_decode(data), format=fmt)
    except Exception as e:
        raise IngestError(f"Cannot parse RDF ({fmt}): {e}") from e
    return g


def graph_to_model(g: Graph, source: str) -> dict:
    classes, dprops, oprops = {}, [], []
    class_uris = set(g.subjects(RDF.type, OWL.Class)) | set(g.subjects(RDF.type, RDFS.Class))
    class_uris |= {o for o in g.objects(None, RDFS.subClassOf) if isinstance(o, URIRef)}
    class_uris |= {s for s in g.subjects(RDFS.subClassOf, None) if isinstance(s, URIRef)}
    inferred_from_instances = False
    if not class_uris:  # instance-only data: derive classes from rdf:type usage
        class_uris = {o for o in g.objects(None, RDF.type) if isinstance(o, URIRef) and o not in (OWL.Ontology,)}
        inferred_from_instances = True
    for u in class_uris:
        if not isinstance(u, URIRef) or str(u).startswith(str(OWL)) or str(u).startswith(str(RDFS)):
            continue
        name = local(u)
        comment = g.value(u, RDFS.comment)
        label = g.value(u, RDFS.label)
        classes[u] = {"name": name, "label": str(label) if label else to_label(name),
                      "comment": str(comment) if comment else "", "parents": [],
                      "source": {"document": source, "iri": str(u)}}
    for c, d in classes.items():
        for p in g.objects(c, RDFS.subClassOf):
            if p in classes:
                d["parents"].append(classes[p]["name"])

    def first(seq):
        return next((x for x in seq if x in classes), None)

    declared = 0
    for kind, rdf_type in (("d", OWL.DatatypeProperty), ("o", OWL.ObjectProperty)):
        for p in g.subjects(RDF.type, rdf_type):
            declared += 1
            dom, rng = first(g.objects(p, RDFS.domain)), None
            label = g.value(p, RDFS.label)
            base = {"name": local(p), "label": str(label) if label else to_label(local(p)),
                    "domain": classes[dom]["name"] if dom else None, "source": {"document": source, "iri": str(p)}}
            if kind == "d":
                r = next(iter(g.objects(p, RDFS.range)), None)
                base["datatype"] = local(r) if r is not None and str(r).startswith(str(XSD)) else "string"
                if base["domain"]:
                    dprops.append({**base, "required": False, "identifier": False})
            else:
                rng = first(g.objects(p, RDFS.range))
                if base["domain"] and rng:
                    oprops.append({**base, "range": classes[rng]["name"], "cardinality": "many-to-many", "required": False})
    if inferred_from_instances or not declared:
        seen_d, seen_o = set(), set()
        type_of = {}
        for s, _, o in g.triples((None, RDF.type, None)):
            if o in classes:
                type_of.setdefault(s, []).append(o)
        for s, p, o in g:
            if p in (RDF.type, RDFS.label, RDFS.comment, RDFS.subClassOf) or s not in type_of:
                continue
            dom = classes[type_of[s][0]]["name"]
            if isinstance(o, Literal):
                key = (dom, local(p))
                if key not in seen_d:
                    seen_d.add(key)
                    dt = local(o.datatype) if o.datatype and str(o.datatype).startswith(str(XSD)) else "string"
                    dprops.append({"name": local(p), "label": to_label(local(p)), "domain": dom, "datatype": dt,
                                   "required": False, "identifier": False, "source": {"document": source, "iri": str(p)}})
            elif o in type_of:
                key = (dom, local(p), classes[type_of[o][0]]["name"])
                if key not in seen_o:
                    seen_o.add(key)
                    oprops.append({"name": local(p), "label": to_label(local(p)), "domain": dom,
                                   "range": key[2], "cardinality": "many-to-many", "required": False,
                                   "source": {"document": source, "iri": str(p)}})
    if not classes:
        raise IngestError("No classes or typed resources found in the RDF data.")
    return {"classes": list(classes.values()), "dataProperties": dprops, "objectProperties": oprops}


def parse_rdf(filename: str, data: bytes) -> dict:
    g = parse_graph(filename, data)
    return {"kind": "model", "model": graph_to_model(g, filename),
            "preview": {"triples": len(g), "text": g.serialize(format="turtle")[:3000]}}
