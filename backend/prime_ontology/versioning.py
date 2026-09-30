"""Versioning & governance (R8): snapshots, diff, workflow, rollback, audit."""
import copy

from django.db import transaction

from .models import AuditEvent, Ontology, OntologyVersion

TRANSITIONS = {  # action -> (from status, to status, capability)
    "submit": ("draft", "review", "submit"),
    "approve": ("review", "approved", "review"),
    "reject": ("review", "draft", "review"),
    "publish": ("approved", "published", "publish"),
}


class GovernanceError(Exception):
    pass


class GovernanceForbidden(GovernanceError):
    """Role lacks the capability for this workflow action (HTTP 403)."""


def audit(o: Ontology | None, action: str, actor: str, **detail):
    AuditEvent.objects.create(ontology=o, action=action, actor=actor, detail=detail)


def next_number(o: Ontology) -> str:
    nums = [tuple(int(x) for x in v.number.split(".")) for v in o.versions.all()]
    if not nums:
        return "1.0"
    major, minor = max(nums)
    return f"{major}.{minor + 1}"


@transaction.atomic
def commit(o: Ontology, message: str, actor: str, major: bool = False) -> OntologyVersion:
    number = next_number(o)
    if major:
        number = f'{int(number.split(".")[0]) + (1 if o.versions.exists() else 0)}.0'
    v = OntologyVersion.objects.create(ontology=o, number=number, snapshot=copy.deepcopy(o.model), status="draft",
                                       message=message or "", created_by=actor)
    o.status = "draft"
    o.save(update_fields=["status", "updated_at"])
    audit(o, "version.commit", actor, version=number, message=message)
    return v


@transaction.atomic
def transition(o: Ontology, v: OntologyVersion, action: str, actor: str, role_ok) -> OntologyVersion:
    if action not in TRANSITIONS:
        raise GovernanceError(f"Unknown action '{action}'.")
    frm, to, cap = TRANSITIONS[action]
    if not role_ok(cap):
        raise GovernanceForbidden(f"Your role cannot {action}.")
    if v.status != frm:
        raise GovernanceError(f"Version {v.number} is '{v.status}'; {action} requires '{frm}'.")
    if action in ("approve", "reject") and v.created_by and v.created_by == actor and actor != "anonymous":
        raise GovernanceError("Four-eyes rule: the author of a version cannot approve or reject it.")
    v.status = to
    if action in ("approve", "reject"):
        v.reviewed_by = actor
    v.save()
    if action == "publish":
        for other in o.versions.filter(status="published").exclude(pk=v.pk):
            other.status = "archived"
            other.save()
        o.current_version = v.number
    latest = o.versions.first()
    if latest and latest.pk == v.pk:
        o.status = to
    o.save()
    audit(o, f"version.{action}", actor, version=v.number)
    return v


@transaction.atomic
def rollback(o: Ontology, v: OntologyVersion, actor: str) -> OntologyVersion:
    o.model = copy.deepcopy(v.snapshot)
    o.save()
    audit(o, "version.rollback", actor, toVersion=v.number)
    return commit(o, f"Rollback to v{v.number}", actor)


# ------------------------------------------------------------------ diff ----

_PROP_FIELDS = ("domain", "range", "datatype", "cardinality", "required", "minCardinality", "maxCardinality", "label", "comment")
_CLASS_FIELDS = ("label", "comment", "parents", "equivalentTo", "disjointWith", "synonyms")


def _props(m):
    d = {("data", p["domain"], p["name"]): p for p in m.get("dataProperties", [])}
    d.update({("object", p["domain"], p["name"]): p for p in m.get("objectProperties", [])})
    return d


def diff(a: dict, b: dict) -> dict:
    """Structural diff a -> b."""
    ca = {c["name"]: c for c in a.get("classes", [])}
    cb = {c["name"]: c for c in b.get("classes", [])}
    added_c, removed_c = sorted(set(cb) - set(ca)), sorted(set(ca) - set(cb))
    pa, pb = _props(a), _props(b)
    renamed = []
    # rename detection: a removed and an added class with (nearly) the same property signature
    def sig(m, cls):
        return frozenset(p["name"] for k, p in _props(m).items() if k[1] == cls)
    for r in list(removed_c):
        for ad in list(added_c):
            sr, sa = sig(a, r), sig(b, ad)
            if sr and sa and len(sr & sa) / len(sr | sa) >= 0.6:
                renamed.append({"from": r, "to": ad})
                removed_c.remove(r)
                added_c.remove(ad)
                break
    changed_c = []
    for n in sorted(set(ca) & set(cb)):
        ch = {f: {"from": ca[n].get(f), "to": cb[n].get(f)} for f in _CLASS_FIELDS if ca[n].get(f) != cb[n].get(f)
              and (ca[n].get(f) or cb[n].get(f))}
        if ch:
            changed_c.append({"name": n, "changes": ch})
    ren_map = {r["from"]: r["to"] for r in renamed}
    added_p = [k for k in pb if k not in pa and not any(pa.get((k[0], r, k[2])) for r in ren_map if ren_map[r] == k[1])]
    removed_p = [k for k in pa if k not in pb and k[1] not in ren_map]
    changed_p = []
    for k in set(pa) & set(pb):
        ch = {f: {"from": pa[k].get(f), "to": pb[k].get(f)} for f in _PROP_FIELDS if pa[k].get(f) != pb[k].get(f)
              and (pa[k].get(f) is not None or pb[k].get(f) is not None)}
        if ch:
            changed_p.append({"kind": k[0], "domain": k[1], "name": k[2], "changes": ch})
    fmt = lambda k: {"kind": k[0], "domain": k[1], "name": k[2]}
    res = {"classes": {"added": added_c, "removed": removed_c, "renamed": renamed, "changed": changed_c},
           "properties": {"added": [fmt(k) for k in sorted(added_p)], "removed": [fmt(k) for k in sorted(removed_p)],
                          "changed": sorted(changed_p, key=lambda x: (x["domain"], x["name"]))}}
    lines = [f"+ Class: {n}" for n in added_c] + [f"- Class: {n}" for n in removed_c]
    lines += [f"~ Class: {r['from']} → {r['to']}" for r in renamed]
    lines += [f"~ Class {c['name']}: " + ", ".join(c["changes"]) for c in changed_c]
    lines += [f"+ {k[0].title()}Property: {k[1]}.{k[2]}" for k in sorted(added_p)]
    lines += [f"- {k[0].title()}Property: {k[1]}.{k[2]}" for k in sorted(removed_p)]
    for c in res["properties"]["changed"]:
        lines.append(f"~ {c['kind'].title()}Property {c['domain']}.{c['name']}: " + ", ".join(
            f"{f} {v['from']} → {v['to']}" for f, v in c["changes"].items()))
    res["lines"] = lines
    res["summary"] = {"changes": len(lines)}
    return res
