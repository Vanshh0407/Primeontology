"""Branch & merge endpoints."""
import copy
import re

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import branching, validation, versioning
from .identity import can, requires
from .models import Ontology
from .views import ApiError, api, body, detail, ensure_draft, get_ontology, summary

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _./-]{0,60}$")


def _root(o: Ontology) -> Ontology:
    return o.branch_of if o.branch_of_id else o


def _family(o: Ontology):
    root = _root(o)
    return root, [root, *root.branches.all().order_by("id")]


def _row(b: Ontology, root: Ontology) -> dict:
    ahead = 0
    if b.base_snapshot is not None:
        ahead = versioning.diff(b.base_snapshot, b.model)["summary"]["changes"]
    return {**summary(b), "isRoot": b.id == root.id, "changesSinceFork": ahead, "baseVersion": b.base_version}


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def branches(request, pk):
    o = get_ontology(request, pk)
    root, fam = _family(o)
    if request.method == "POST":
        if not can(request.identity["role"], "write"):
            raise ApiError("Role cannot create branches.", 403)
        b = body(request)
        name = str(b.get("name", "")).strip()
        if not NAME.match(name) or name.lower() == "main":
            raise ApiError("Branch name: letters, digits, space, _ . / - (max 60); 'main' is reserved.")
        if any(x.branch_name.lower() == name.lower() for x in fam):
            raise ApiError(f"A branch named '{name}' already exists.")
        base_model, base_version = o.model, ""
        if b.get("fromVersion"):
            v = o.versions.filter(number=b["fromVersion"]).first()
            if not v:
                raise ApiError("Version not found.", 404)
            base_model, base_version = v.snapshot, v.number
        nb = Ontology.objects.create(
            name=f"{root.name} ⎇ {name}", base_iri=root.base_iri, context=root.context, tenant=root.tenant, source_type=root.source_type,
            model=copy.deepcopy(base_model), branch_of=root, branch_name=name, base_version=base_version,
            base_snapshot=copy.deepcopy(base_model))
        versioning.audit(nb, "branch.created", request.identity["user"], root=root.id, fromOntology=o.id, fromVersion=base_version or "working copy")
        versioning.audit(root, "branch.created", request.identity["user"], branch=name, id=nb.id)
        return JsonResponse(detail(nb), status=201)
    return JsonResponse({"root": root.id, "branches": [_row(x, root) for x in fam]})


@api
@requires("read")
@require_http_methods(["POST"])
def merge(request, pk):
    """Merge branch `sourceId` into ontology <pk>. dryRun previews; resolutions = {conflictId: 'ours'|'theirs'}."""
    target = get_ontology(request, pk)
    b = body(request)
    source = get_ontology(request, b.get("sourceId", -1))
    if source.id == target.id:
        raise ApiError("Cannot merge an ontology into itself.")
    if _root(source).id != _root(target).id:
        raise ApiError("Only branches of the same ontology can be merged.")
    dry = bool(b.get("dryRun", True))
    if not dry and not can(request.identity["role"], "write"):
        raise ApiError("Role cannot merge branches.", 403)
    res_in = b.get("resolutions") or {}
    if not isinstance(res_in, dict) or any(v not in ("ours", "theirs") for v in res_in.values()):
        raise ApiError("resolutions must map conflict ids to 'ours' or 'theirs'.")
    base = source.base_snapshot if source.base_snapshot is not None else target.model
    result = branching.three_way(base, target.model, source.model, res_in)
    out = {"target": target.id, "source": source.id, "conflicts": result["conflicts"], "resolvedConflicts": result["resolvedConflicts"],
           "merged": False, "dryRun": dry}
    if result["model"] is None:
        out["message"] = f"{len(result['conflicts'])} conflict(s) need a decision (keep yours or take the branch's) before merging."
        return JsonResponse(out)
    diff = versioning.diff(target.model, result["model"])
    v = validation.validate(result["model"])
    out.update(changes=diff["lines"], validation={k: v[k] for k in ("health", "errors", "warnings")},
               message="No conflicts." if not diff["lines"] else f"{len(diff['lines'])} change(s) will be applied to the target.")
    if dry:
        return JsonResponse(out)
    if v["errors"] > validation.validate(target.model)["errors"] and not b.get("allowErrors"):
        raise ApiError("The merged ontology would have more validation errors than the target. Review them (dry run) or pass allowErrors.", 409,
                       {**out, "code": "merge-introduces-errors"})
    target.model = result["model"]
    ensure_draft(target, request.identity["user"])
    target.save()
    # Like a git merge-base: the next merge of this branch starts from the branch as it was NOW (what we just took in),
    # not from the merged result — otherwise changes made on the target would look like deletions made on the branch.
    source.base_snapshot = copy.deepcopy(source.model)
    source.merged_at = timezone.now()
    source.save()
    versioning.audit(target, "branch.merged", request.identity["user"], source=source.id, branch=source.branch_name or source.name,
                     changes=diff["lines"][:50], total=len(diff["lines"]), resolved=result["resolvedConflicts"])
    out.update(merged=True, ontology=detail(target))
    return JsonResponse(out)
