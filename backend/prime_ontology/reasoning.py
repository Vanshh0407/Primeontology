"""Reasoning (RDFS/OWL-RL) and SHACL."""
from rdflib import OWL, RDF, RDFS, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import SH

from . import generator
from .validation import ancestors


def inferred_hierarchy(model: dict) -> list[dict]:
    """Transitive superclass closure minus asserted edges."""
    out = []
    for c in model["classes"]:
        asserted = set(c.get("parents", []))
        for a in sorted(ancestors(model, c["name"]) - asserted - {c["name"]}):
            via = next((p for p in asserted if a in ancestors(model, p)), None)
            out.append({"subject": c["name"], "predicate": "subClassOf", "object": a, "via": via, "rule": "rdfs11 (transitivity)"})
    return out


def inherited_properties(model: dict, cls: str) -> list[dict]:
    anc = ancestors(model, cls)
    res = []
    for kind, key in (("data", "dataProperties"), ("object", "objectProperties")):
        for p in model[key]:
            if p.get("domain") in anc:
                res.append({**p, "kind": kind, "inheritedFrom": p["domain"]})
    return res


def reason(model: dict, profile: str = "owlrl", base_iri: str = generator.DEFAULT_BASE_IRI) -> dict:
    """Run RDFS/OWL-RL closure; report inferred hierarchy, types, and consistency."""
    import owlrl

    g = generator.model_to_graph(model, "reasoning", base_iri)
    before = len(g)
    closure = owlrl.DeductiveClosure(owlrl.RDFS_Semantics if profile == "rdfs" else owlrl.OWLRL_Semantics)
    closure.expand(g)
    ns = Namespace(base_iri)
    names = {c["name"] for c in model["classes"]}
    unsat = sorted(str(s).rsplit("#", 1)[-1] for s in g.subjects(RDFS.subClassOf, OWL.Nothing)
                   if str(s).rsplit("#", 1)[-1] in names)
    errors = [str(o) for o in g.objects(None, URIRef("http://www.daml.org/2002/03/agents/agent-ont#error"))]
    inferred_types = []
    for ind in model.get("individuals", []):
        types = sorted(str(o).rsplit("#", 1)[-1] for o in g.objects(ns[ind["name"]], RDF.type)
                       if str(o).startswith(base_iri) and str(o).rsplit("#", 1)[-1] != ind["name"])
        extra = [t for t in types if t != ind["class"]]
        if extra:
            inferred_types.append({"individual": ind["name"], "asserted": ind["class"], "inferred": extra})
    return {"profile": profile, "triplesBefore": before, "triplesAfter": len(g),
            "inferredHierarchy": inferred_hierarchy(model), "inferredIndividualTypes": inferred_types,
            "unsatisfiableClasses": unsat, "consistent": not unsat and not errors,
            "inconsistencies": [e.replace(base_iri, "") for e in errors]}


def build_shapes(model: dict, base_iri: str = generator.DEFAULT_BASE_IRI) -> Graph:
    """Generate SHACL NodeShapes from the ontology."""
    ns = Namespace(base_iri)
    shp = Namespace(base_iri.rstrip("#/") + "/shapes#")
    g = Graph()
    g.bind("sh", SH)
    g.bind("onto", ns)
    g.bind("shape", shp)
    g.bind("xsd", XSD)
    for c in model["classes"]:
        ns_ = shp[c["name"] + "Shape"]
        g.add((ns_, RDF.type, SH.NodeShape))
        g.add((ns_, SH.targetClass, ns[c["name"]]))
        for p in model["dataProperties"]:
            if p["domain"] != c["name"]:
                continue
            ps = BNode()
            g.add((ns_, SH.property, ps))
            g.add((ps, SH.path, ns[generator.prop_local(model, p)]))
            g.add((ps, SH.datatype, XSD[p["datatype"]]))
            mn = p.get("minCardinality", 1 if p.get("required") else None)
            mx = p.get("maxCardinality", 1 if p.get("identifier") else None)
            if mn not in (None, "", 0):
                g.add((ps, SH.minCount, Literal(int(mn))))
            if mx not in (None, ""):
                g.add((ps, SH.maxCount, Literal(int(mx))))
        for p in model["objectProperties"]:
            if p["domain"] != c["name"]:
                continue
            ps = BNode()
            g.add((ns_, SH.property, ps))
            g.add((ps, SH.path, ns[generator.prop_local(model, p)]))
            g.add((ps, SH["class"], ns[p["range"]]))
            mn = p.get("minCardinality", 1 if p.get("required") else None)
            mx = p.get("maxCardinality", 1 if p.get("cardinality") == "many-to-one" else None)
            if mn not in (None, "", 0):
                g.add((ps, SH.minCount, Literal(int(mn))))
            if mx not in (None, ""):
                g.add((ps, SH.maxCount, Literal(int(mx))))
    return g


def shacl_validate(model: dict, data_ttl: str | None, base_iri: str = generator.DEFAULT_BASE_IRI) -> dict:
    """Validate a data graph against shapes generated from the ontology.
    With no data, validates the model's own individuals."""
    import pyshacl

    shapes = build_shapes(model, base_iri)
    data = Graph()
    if data_ttl:
        try:
            data.parse(data=data_ttl, format="turtle")
        except Exception as e:
            raise ValueError(f"Cannot parse data graph: {e}") from e
    else:
        data = generator.model_to_graph(model, "data", base_iri)
    ont = generator.model_to_graph(model, "ontology", base_iri)
    conforms, rgraph, text = pyshacl.validate(data, shacl_graph=shapes, ont_graph=ont, inference="rdfs", advanced=True)
    results = []
    for r in rgraph.subjects(RDF.type, SH.ValidationResult):
        results.append({
            "focusNode": str(rgraph.value(r, SH.focusNode)).rsplit("#", 1)[-1],
            "path": str(rgraph.value(r, SH.resultPath) or "").rsplit("#", 1)[-1],
            "message": str(rgraph.value(r, SH.resultMessage) or ""),
            "constraint": str(rgraph.value(r, SH.sourceConstraintComponent) or "").rsplit("#", 1)[-1],
        })
    return {"conforms": bool(conforms), "violations": results, "report": text[:4000]}
