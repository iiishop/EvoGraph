"""Small, server-scheduled PlanDelta work units; no read-authorization protocol.

The manifest contains identities, field names and references only. Each unit is
still compiled and validated by the existing whole-candidate planning services.
Scheduling is not semantic verification or permission to weaken a requirement.
"""
import hashlib
import json
from copy import deepcopy
from typing import Literal, get_args

from pydantic import Field, model_validator

from ..agent_tools.base import ToolSpec
from ..domain.models import Model, Project
from ..domain.plan_contracts import active_behaviors, candidate_hash, planning_payload
from .plan_ir import PlanDelta, architecture_delta_fields, required_plan_fields

MAX_UNIT_BYTES = 6144
MAX_UNIT_CONTRACTS = 3
MAX_UNIT_OPERATIONS = 8
SIZE_HOLD = "hard atomic group exceeds estimated 6 KiB, 3 contracts or 8 operations; held without splitting"
COLLECTIONS = {
    "requirement": ("requirements", "id"), "process_constraint": ("process_constraints", "id"),
    "retire_requirement": ("retire_requirements", "id"), "contract": ("contracts", "key"),
    "slice": ("slices", "id"), "component": ("components", "id"),
    "retirement": ("retirements", "component_id"),
}
REMOVALS = {"remove_contract": "remove_contract_keys", "remove_slice": "remove_slice_ids",
            "remove_component": "remove_component_ids"}
ARCH_FIELDS = architecture_delta_fields()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _identity(project):
    payload = planning_payload(project)
    for source in payload["plan_contract"]["sources"]:
        source.pop("activity", None)
        source.pop("message_id", None)
    return _hash(payload)


def _key(kind, identity):
    return kind + ":" + identity


class ManifestUse(Model):
    field: str = Field(min_length=1, max_length=40)
    kind: Literal["requirement", "contract", "slice", "component", "capability"]
    id: str = Field(min_length=1, max_length=100)


class ManifestChange(Model):
    kind: Literal["requirement", "process_constraint", "retire_requirement", "contract", "slice",
                  "component", "relation", "remove_relation", "remove_contract", "remove_slice",
                  "remove_component", "retirement", "target", "architecture"]
    id: str = Field(min_length=1, max_length=300)
    fields: list[str] = Field(default_factory=list, max_length=24)
    uses: list[ManifestUse] = Field(default_factory=list, max_length=100)
    intent: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_fields(self):
        _validate_manifest_change(self.model_dump())
        return self


class SchedulePlanChanges(Model):
    changes: list[ManifestChange] = Field(min_length=1, max_length=128)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)
        common = schema["$defs"]["ManifestChange"]["properties"]
        branches = []
        for kind, spec in manifest_field_catalog().items():
            fields = {"type": "array", "uniqueItems": True,
                      "maxItems": min(24, len(spec["fields"])),
                      "items": {"type": "string"},
                      "description": "Changed fields only for existing IDs. Later patch payload requires: "
                      + ",".join(spec["required_existing"])
                      + "; manifest required for new: " + ",".join(spec["required_new"])}
            if spec["fields"]:
                fields.update(minItems=1, items={"type": "string", "enum": spec["fields"]})
            uses = {"type": "array", "maxItems": 100 if spec["references"] else 0,
                    "items": {"type": "object", "properties": {}, "additionalProperties": False}}
            if spec["references"]:
                uses["items"] = {"anyOf": [{
                    "type": "object", "additionalProperties": False,
                    "properties": {"field": {"type": "string", "const": field}, "kind": {"type": "string", "const": target},
                                   "id": {"type": "string", "minLength": 1, "maxLength": 100}},
                    "required": ["field", "kind", "id"],
                } for field, targets in spec["references"].items() for target in targets]}
            identity = {k: v for k, v in common["id"].items() if k != "title"}
            if kind in {"target", "architecture"}:
                identity["const"] = kind
            branches.append({"type": "object", "additionalProperties": False,
                "properties": {"kind": {"type": "string", "const": kind}, "id": identity, "fields": fields,
                               "uses": uses, "intent": {k: v for k, v in common["intent"].items() if k != "title"}},
                "required": ["kind", "id", "fields", "intent"]})
        schema["properties"]["changes"]["items"] = {"anyOf": branches}
        # The provider sees only the constrained branches, never the permissive
        # internal transport model or an unused broad ManifestUse definition.
        schema.pop("$defs", None)
        return schema


def _kind_fields(kind):
    if kind in COLLECTIONS:
        collection, identity = COLLECTIONS[kind]
        model = get_args(PlanDelta.model_fields[collection].annotation)[0]
        return model, {name: field for name, field in model.model_fields.items() if name != identity}
    if kind in {"relation", "remove_relation"}:
        collection = "relations" if kind == "relation" else "remove_relations"
        model = get_args(PlanDelta.model_fields[collection].annotation)[0]
        return model, model.model_fields
    names = {"target"} if kind == "target" else ARCH_FIELDS if kind == "architecture" else set()
    return PlanDelta, {name: PlanDelta.model_fields[name] for name in sorted(names)}


def manifest_field_catalog():
    """Project-free catalog derived from the exact compiler input models."""
    result = {}
    for kind in get_args(ManifestChange.model_fields["kind"].annotation):
        model, fields = _kind_fields(kind)
        allowed = set(fields)
        for field in PlanDelta.model_fields.values():
            extra = (field.json_schema_extra or {}).get("manifest_extra", {})
            if extra.get("kind") == kind:
                allowed.add(extra["field"])
        required = required_plan_fields(model) & fields.keys()
        if kind == "target":
            required = set(fields)
        result[kind] = {"fields": sorted(allowed), "required_existing": sorted(required),
            "required_new": sorted(required | (required_plan_fields(model, new=True) & fields.keys())),
            "references": {name: sorted({ref["kind"] for ref in (field.json_schema_extra or {}).get("references", [])})
                           for name, field in fields.items() if (field.json_schema_extra or {}).get("references")}}
    return result


def _validate_manifest_change(row, *, new=False):
    kind, identity = row["kind"], row["id"]
    spec = manifest_field_catalog()[kind]
    fields = set(row["fields"])
    # Existing unchanged required payload fields are supplied to the unit from
    # its current object; a short scheduling intent need not call them changes.
    # Actual PlanDelta required fields remain enforced at compilation.
    required = set(spec["required_new"]) if new else set()
    invalid, missing = fields - set(spec["fields"]), required - fields
    if invalid or missing or (not fields and spec["fields"]):
        raise ValueError(f"{kind}:{identity} fields invalid={sorted(invalid)} missing={sorted(missing)}; "
                         f"allowed={spec['fields']}; required_existing={spec['required_existing']}; "
                         f"required_new={spec['required_new']}; omitted existing fields stay unchanged")
    if kind in {"target", "architecture"} and identity != kind:
        raise ValueError(f"{kind} manifest id must be {kind}")
    for use in row["uses"]:
        if use["kind"] not in spec["references"].get(use["field"], []):
            raise ValueError(f"{kind}:{identity} invalid uses={use}; allowed reference slots={spec['references']}; "
                             "source_id is a source anchor, not a graph use")


def _objects(project):
    active = active_behaviors(project)
    bindings = {b.behavior_key: b for b in project.plan_contract.bindings}
    objects = {}
    for kind, values in (("slice", project.milestones), ("requirement", project.plan_contract.requirements),
                         ("process_constraint", project.plan_contract.process_constraints)):
        for value in values:
            objects[_key(kind, value.id)] = value.model_dump()
    for node in project.source_milestones:
        objects[_key("slice", node.id)] = {**node.model_dump(), "read_only": True}
    for key, behavior in active.items():
        objects[_key("contract", key)] = {
            "key": key, "owner": behavior.owner, "statement": behavior.statement,
            "acceptance_scope": behavior.acceptance_scope, "revision_id": behavior.id,
            **(bindings[key].model_dump(exclude={"behavior_key", "behavior_revision_id"}) if key in bindings else {}),
        }
    for key, binding in bindings.items():
        if key in active:
            for capability in binding.provides:
                objects[_key("capability", capability.key)] = {**capability.model_dump(), "provider": key}
    if project.architectures:
        architecture = project.architectures[-1]
        objects["architecture:architecture"] = architecture.model_dump()
        for component in architecture.diagram.nodes:
            objects[_key("component", component.id)] = component.model_dump()
        for edge in architecture.diagram.edges:
            objects[_key("relation", _json([edge.source, edge.target, edge.label]))] = edge.model_dump()
    if project.targets:
        objects["target:target"] = project.targets[-1].model_dump()
    return objects


def _references(kind, value):
    result = []
    def identities(data, path):
        if isinstance(data, list):
            return [identity for item in data for identity in identities(item, path)]
        if path:
            return identities(data.get(path), "") if isinstance(data, dict) else []
        return [data] if isinstance(data, str) else []
    _, fields = _kind_fields(kind)
    for name, field in fields.items():
        for ref in (field.json_schema_extra or {}).get("references", []):
            result.extend({"field": name, "kind": ref["kind"], "id": identity}
                          for identity in identities(value.get(name), ref.get("path", "")))
    return result


def _manifest_rows(args, project=None):
    rows = [c.model_dump() for c in args.changes]
    objects = _objects(project) if project is not None else None
    seen = set()
    for row in rows:
        kind, identity = row["kind"], row["id"]
        if kind in {"relation", "remove_relation"}:
            try:
                values = json.loads(identity)
            except ValueError:
                values = None
            if not isinstance(values, list) or len(values) != 3 or not all(isinstance(v, str) and v for v in values):
                raise ValueError('relation id must be the JSON triple [source,target,label]')
            row["id"] = identity = _json(values)
        key = _key(kind, identity)
        if key in seen or len(row["fields"]) != len(set(row["fields"])):
            raise ValueError("manifest contains duplicate identity or field: " + key)
        seen.add(key)
        _validate_manifest_change(row, new=objects is not None and key not in objects)
        row["change_id"] = key
    return rows


def _schedule(project, rows):
    """Union only necessary companions; other new-reference edges stay ordered."""
    objects = _objects(project)
    by_key = {r["change_id"]: i for i, r in enumerate(rows)}
    parent = list(range(len(rows)))
    problems = []
    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    def join(a, b):
        if a is not None and b is not None:
            parent[root(b)] = root(a)
    def row_id(kind, identity):
        return by_key.get(_key(kind, identity))
    def need(index, kind, identity, field=None):
        companion = row_id(kind, identity)
        if companion is None or (field and field not in rows[companion]["fields"]):
            problems.append((index, f"missing atomic repair: {kind}:{identity}" + (f".{field}" if field else "")))
        else:
            join(index, companion)
    def removed_contract(key):
        direct = row_id("remove_contract", key)
        owner = objects.get(_key("contract", key), {}).get("owner")
        return direct if direct is not None else row_id("remove_slice", owner)
    contracts = {key.split(":", 1)[1]: value for key, value in objects.items() if key.startswith("contract:")}
    def proposed_refs(row, field):
        if field in row["fields"]:
            return [u["id"] for u in row["uses"] if u["field"] == field]
        old = contracts.get(row["id"], {})
        return [old["owner"]] if field == "owner" and "owner" in old else old.get(field, [])
    def repair_contract(index, key, field):
        removal = removed_contract(key)
        if removal is not None:
            join(index, removal)
        else:
            need(index, "contract", key, field)
    # Definitions are lookup-only: shared requirements do not union all consumers.
    definitions = {r["change_id"]: i for i, r in enumerate(rows)
                   if r["kind"] in {"requirement", "slice", "contract", "component"}}
    for i, row in enumerate(rows):
        for use in row["uses"]:
            if use["field"] == "provides":
                key = _key("capability", use["id"])
                if key in definitions:
                    raise ValueError("duplicate capability declaration in manifest")
                definitions[key] = i
    for i, row in enumerate(rows):
        kind, identity = row["kind"], row["id"]
        key = _key(kind, identity)
        if kind in {"requirement", "slice"} and key not in objects:
            field = "requirement_ids" if kind == "requirement" else "owner"
            first = next((j for j, other in enumerate(rows) if other["kind"] == "contract" and any(
                u["kind"] == kind and u["id"] == identity and u["field"] == field for u in other["uses"])), None)
            if first is None:
                problems.append((i, "new " + kind + " needs its first covering/owned contract"))
            else:
                join(i, first)
        if kind == "retire_requirement":
            for ckey, contract in contracts.items():
                if identity in contract.get("requirement_ids", []):
                    repair_contract(i, ckey, "requirement_ids")
        deleted_contracts = ([identity] if kind == "remove_contract" else
                             [key for key, c in contracts.items() if c["owner"] == identity] if kind == "remove_slice" else [])
        if kind == "slice" and any(m.id == identity for m in project.source_milestones):
            problems.append((i, "source slices are read-only"))
        for deleted in deleted_contracts:
            old_contract = contracts.get(deleted, {})
            owner = old_contract.get("owner")
            if owner and not any(c["owner"] == owner and removed_contract(k) is None for k, c in contracts.items()):
                owner_removal = row_id("remove_slice", owner)
                replacement = next((j for j, r in enumerate(rows) if r["kind"] == "contract"
                                    and owner in proposed_refs(r, "owner") and removed_contract(r["id"]) is None), None)
                if owner_removal is not None:
                    join(i, owner_removal)
                elif replacement is not None:
                    join(i, replacement)
                else:
                    problems.append((i, "last owned contract needs owner deletion or replacement: " + owner))
            if old_contract.get("acceptance_scope") == "target":
                for rid in old_contract.get("requirement_ids", []):
                    if any(c.get("acceptance_scope") == "target" and rid in c.get("requirement_ids", [])
                           and removed_contract(k) is None for k, c in contracts.items()):
                        continue
                    retirement = row_id("retire_requirement", rid)
                    replacement = next((j for j, r in enumerate(rows) if r["kind"] == "contract"
                                        and rid in proposed_refs(r, "requirement_ids") and removed_contract(r["id"]) is None), None)
                    if retirement is not None:
                        join(i, retirement)
                    elif replacement is not None:
                        join(i, replacement)
                    else:
                        problems.append((i, "last target coverage needs retirement or replacement: " + rid))
            for ckey, contract in contracts.items():
                if deleted in contract.get("requires_behavior_keys", []):
                    repair_contract(i, ckey, "requires_behavior_keys")
            caps = {c["key"] for c in contracts.get(deleted, {}).get("provides", [])}
            for ckey, contract in contracts.items():
                if any(s.get("capability_key") in caps for s in contract.get("steps") or []):
                    repair_contract(i, ckey, "steps")
        if kind == "remove_slice":
            for milestone in project.milestones:
                if identity in milestone.dependencies:
                    removal = row_id("remove_slice", milestone.id)
                    if removal is not None:
                        join(i, removal)
                    else:
                        need(i, "slice", milestone.id, "dependencies")
        if kind == "remove_component":
            need(i, "retirement", identity)
            for ckey, contract in contracts.items():
                if identity in contract.get("component_ids", []):
                    repair_contract(i, ckey, "component_ids")
            for obj, edge in objects.items():
                if obj.startswith("relation:") and identity in {edge["source"], edge["target"]}:
                    need(i, "remove_relation", obj.split(":", 1)[1])
        if project.architectures and kind in {"remove_slice", "remove_component"}:
            diagram = project.architectures[-1].diagram
            if kind == "remove_slice" and identity in diagram.milestone_ids:
                need(i, "architecture", "architecture", "architecture_milestone_ids")
            if kind == "remove_component" and any(identity in g.member_node_ids for g in diagram.groups):
                need(i, "architecture", "architecture", "architecture_groups")
    if not project.architectures and any(r["kind"] in {"component", "architecture", "relation"} for r in rows):
        architecture = row_id("architecture", "architecture")
        component = next((i for i, r in enumerate(rows) if r["kind"] == "component"), None)
        if architecture is None or component is None:
            problems.append((architecture if architecture is not None else component or 0,
                             "new architecture needs summary, technologies and first component together"))
        else:
            join(architecture, component)
            required = set(manifest_field_catalog()["architecture"]["required_new"])
            if not required <= set(rows[architecture]["fields"]):
                problems.append((architecture, "new architecture requires fields: " + ", ".join(sorted(required))))
    # Unknown/new references order units. Existing references need no artificial batching.
    dependencies = {root(i): set() for i in range(len(rows))}
    for i, row in enumerate(rows):
        for use in row["uses"]:
            if use["field"] == "provides":
                continue
            key = _key(use["kind"], use["id"])
            if key in objects or (use["kind"] == "slice" and any(m.id == use["id"] for m in project.source_milestones)):
                continue
            defined = definitions.get(key)
            if defined is None:
                problems.append((i, "undefined manifest reference: " + key))
            elif root(defined) != root(i):
                dependencies[root(i)].add(root(defined))
    reach = {}
    for start in dependencies:
        seen, pending = set(), list(dependencies[start])
        while pending:
            node = pending.pop()
            if node not in seen:
                seen.add(node)
                pending.extend(dependencies.get(node, set()) - seen)
        reach[start] = seen
    for a in dependencies:
        for b in reach[a]:
            if a in reach.get(b, set()):
                join(a, b)
    collapsed = {root(i): set() for i in range(len(rows))}
    for a, targets in dependencies.items():
        collapsed[root(a)].update(root(b) for b in targets if root(b) != root(a))
    dependencies = collapsed
    groups = {r: [i for i in range(len(rows)) if root(i) == r] for r in dependencies}
    order, remaining = [], set(groups)
    while remaining:
        ready = sorted((r for r in remaining if not (dependencies[r] & remaining)), key=lambda r: min(groups[r]))
        if not ready:
            for r in sorted(remaining):
                problems.append((r, "cyclic new-definition dependencies; no safe small unit order"))
            order.extend(sorted(remaining))
            break
        order.extend(ready)
        remaining.difference_update(ready)
    units, group_units = [], {}
    for group in order:
        indexes = groups[group]
        entries = [rows[i] for i in indexes]
        object_keys = {_key(r["kind"].removeprefix("remove_"), r["id"]) for r in entries}
        old_bytes = sum(len(_json(objects[k]).encode()) for k in object_keys if k in objects)
        contract_count = sum(r["kind"] in {"contract", "remove_contract"} for r in entries)
        estimated_bytes = old_bytes + 512 * sum(k not in objects for k in object_keys)
        reasons = [message for index, message in problems if root(index) == group]
        # An indivisible record may exceed the packing target. Keep it alone;
        # actual provider byte budgets and complete-delta validation still apply.
        if ((estimated_bytes > MAX_UNIT_BYTES and len(entries) > 1)
                or contract_count > MAX_UNIT_CONTRACTS or len(entries) > MAX_UNIT_OPERATIONS):
            reasons.append(SIZE_HOLD)
        depends = {group_units[d] for d in dependencies[group] if d in group_units}
        # Pack independent or already-ordered small groups without merging their semantics.
        previous = units[-1] if units else None
        if (not reasons and previous and previous["state"] == "pending"
                and previous["estimated_text_bytes"] + estimated_bytes <= MAX_UNIT_BYTES
                and previous["contract_count"] + contract_count <= MAX_UNIT_CONTRACTS
                and len(previous["changes"]) + len(entries) <= MAX_UNIT_OPERATIONS):
            unit = previous
            unit["changes"].extend(entries)
            unit["existing_text_bytes"] += old_bytes
            unit["estimated_text_bytes"] += estimated_bytes
            unit["contract_count"] += contract_count
            unit["depends_on"] = sorted((set(unit["depends_on"]) | depends) - {unit["id"]})
        else:
            unit = {"id": f"unit-{len(units) + 1:03d}", "state": "held" if reasons else "pending",
                    "changes": entries, "existing_text_bytes": old_bytes, "estimated_text_bytes": estimated_bytes,
                    "contract_count": contract_count,
                    "depends_on": sorted(depends), "holds": reasons}
            units.append(unit)
        group_units[group] = unit["id"]
    for unit in units:
        unit["hash"] = _hash(unit["changes"])
    return units


def _valid_schedule(record, project, source_id):
    schedule = record.get("work_units")
    return bool(schedule and schedule.get("source_id") == source_id and schedule.get("project_id") == project.id
                and schedule.get("expected_revision") == project.revision
                and schedule.get("expected_fingerprint") == _identity(project))


def _next(schedule):
    completed = set(schedule.get("completed_ids", []))
    return next((u for u in schedule["units"] if u["state"] == "pending"
                 and set(u["depends_on"]) <= completed), None)


def schedule_plan_changes(ctx, args):
    db = ctx.application.db
    if not getattr(db, "is_candidate", False):
        raise ValueError("work units require a planning candidate")
    project = db.get(ctx.project_id)
    rows = _manifest_rows(args, project)
    manifest_hash = _hash(rows)
    old = db.record.get("work_units")
    if _valid_schedule(db.record, project, db.source_id) and old["manifest_hash"] == manifest_hash:
        return {"status": "NO_PROGRESS", "schedule": _schedule_view(old)}
    if old and old.get("source_id") == db.source_id and old.get("completed_ids"):
        raise ValueError("this request already has completed units; preserve them and rebuild only on a new user direction")
    source = next(s for s in project.plan_contract.sources if s.id == db.source_id)
    units = _schedule(project, rows)
    schedule = {"version": "plan-units/v1", "project_id": project.id, "source_id": db.source_id,
                "origin_source_id": db.source_id, "input_text": source.text, "manifest": rows,
                "manifest_hash": manifest_hash, "base_fingerprint": _identity(project),
                "expected_revision": project.revision, "expected_fingerprint": _identity(project),
                "units": units, "completed_ids": [], "pending_ids": [u["id"] for u in units], "checkpoints": []}
    record = {**db.record, "work_units": schedule, "unit_request": None}
    db.record = db.store.save(record)
    return {"node_ids": [], "status": "scheduled", "schedule": _schedule_view(schedule)}


def _schedule_view(schedule):
    return {"manifest_hash": schedule["manifest_hash"], "completed_ids": list(schedule["completed_ids"]),
            "pending_ids": list(schedule["pending_ids"]),
            "units": [{k: deepcopy(u[k]) for k in ("id", "hash", "state", "contract_count", "existing_text_bytes", "estimated_text_bytes", "depends_on", "holds")}
                      for u in schedule["units"]]}


def initial_unit_context(db):
    project = db.get(db.project.id)
    objects = _objects(project)
    return _initial_unit_context(db, project, objects)


def carry_review_findings(previous):
    """Carry historical issue text, never a pass or reusable certificate.

    Interrupted repair can have no accepted schedule. Its next router still
    needs the exact problems that caused repair, without copying all supported
    statuses or treating old packet pointers as references into a new snapshot.
    """
    batch = previous.get("report", {}).get("semantic_batch")
    if not isinstance(batch, dict):
        batch = next((item["batch"] for item in reversed(previous.get("batch_reviews", []))
                      if isinstance(item.get("batch"), dict)), None)
    if not isinstance(batch, dict):
        return deepcopy(previous.get("inherited_semantic_findings"))
    return {
        "source_candidate_id": previous["id"], "candidate_hash": batch["candidate_hash"],
        "review_scope_hash": batch["review_scope_hash"],
        "applicability": "historical_needs_recheck",
        "issues": [{k: deepcopy(issue[k]) for k in
                    ("id", "subjects", "verdict", "reason", "counterexample") if k in issue}
                   for issue in batch.get("issues", [])],
        "evidence_note": "Original evidence refs remain in the source candidate's exact review packet. "
                         "These historical issue descriptions are not current proof; recheck against current facts and user changes.",
    }


def _context_sources(project):
    """Transmit each exact source text once; keep every source's own metadata."""
    sources, first = [], {}
    for source in project.plan_contract.sources:
        row = source.model_dump(exclude={"activity"})
        if source.text in first:
            row.pop("text")
            row["same_text_as_source_id"] = first[source.text]
        else:
            first[source.text] = source.id
        sources.append(row)
    return sources


def _initial_unit_context(db, project, objects):
    inactive = {}
    active_keys = {key.split(":", 1)[1] for key in objects if key.startswith("contract:")}
    for behavior in project.behaviors:
        if behavior.behavior_key not in active_keys:
            inactive[behavior.behavior_key] = behavior.model_dump()
    directory = [{"key": c["key"], "owner": c["owner"], "revision_id": c["revision_id"],
                  "statement": c["statement"], "acceptance_scope": c["acceptance_scope"],
                  **{field: deepcopy(c[field]) for field in ("requirement_ids", "component_ids",
                      "requires_behavior_keys", "provides", "steps") if field in c}}
                 for key, c in objects.items() if key.startswith("contract:")]
    completed_keys = set()
    for schedule in (db.record.get("prior_work_units"), db.record.get("work_units")):
        if not isinstance(schedule, dict) or schedule.get("project_id") != project.id:
            continue
        completed = {u["id"]: u["hash"] for u in schedule.get("units", [])
                     if u.get("state") == "completed" and u["id"] in schedule.get("completed_ids", [])}
        for checkpoint in schedule.get("checkpoints", []):
            if checkpoint.get("unit_id") in completed and checkpoint.get("unit_hash") == completed[checkpoint["unit_id"]]:
                completed_keys.update(key for key in checkpoint.get("change_ids", []) if key.startswith("contract:"))
    mechanisms = [{"key": objects[key]["key"], "revision_id": objects[key]["revision_id"],
                   "mechanism": objects[key]["mechanism"]}
                  for key in sorted(completed_keys) if key in objects and "mechanism" in objects[key]]
    components = [{field: value[field] for field in ("id", "label", "description", "role") if field in value}
                  for key, value in objects.items() if key.startswith("component:")]
    responsibilities_included = len(_json(components).encode("utf-8")) < 5000
    if not responsibilities_included:
        components = [{"id": value["id"], "label": value["label"]} for value in components]
    return {"project_id": project.id, "revision": project.revision, "candidate_hash": candidate_hash(project),
            "manifest_field_catalog": manifest_field_catalog(),
            "manifest_field_note": "Use only this PlanDelta catalog, not rich Project/UI fields. Required_new applies only to new IDs; existing omissions retain data. source_id is an exact source anchor, not a uses graph reference.",
            "sources": _context_sources(project),
            "source_text_note": "same_text_as_source_id reuses the exact literal text of that earlier source in this snapshot. "
                                "Resolve it before quoting; IDs and all other metadata remain independent. No source text is omitted.",
            "requirements": [r.model_dump() for r in project.plan_contract.requirements],
            "process_constraints": [c.model_dump() for c in project.plan_contract.process_constraints],
            "target": project.targets[-1].model_dump() if project.targets else None,
            "target_draft": project.target_draft, "acceptance_directory": directory,
            "inactive_behavior_history": list(inactive.values()),
            "slices": [{"id": m.id, "title": m.title, "dependencies": list(m.dependencies)} for m in project.milestones],
            "components": components,
            "components_note": ("Read-only current component responsibilities, included exactly."
                                if responsibilities_included else
                                "Global component responsibilities omitted because their complete directory exceeds 5,000 UTF-8 bytes; current-unit referenced component objects retain full details. No responsibility text is truncated."),
            "completed_contract_mechanisms": mechanisms,
            "completed_contract_mechanisms_note": "Current active mechanisms for contracts touched in completed units, joined by key and revision_id to acceptance_directory in this same snapshot. These are saved planning facts, not execution or semantic validation evidence.",
            "capabilities": [{"id": k.split(":", 1)[1], **v} for k, v in objects.items() if k.startswith("capability:")],
            "source_slices": [m.model_dump(include={"id", "title", "dependencies", "source_behaviors"})
                              for m in project.source_milestones],
            "relations": [{"id": k.split(":", 1)[1], **v} for k, v in objects.items() if k.startswith("relation:")],
            "reference_context": deepcopy(db.record.get("reference_context", {})),
            "findings": deepcopy(db.record.get("report", {}).get("findings", [])),
            "inherited_findings": deepcopy(db.record.get("inherited_findings", [])),
            "inherited_semantic_findings": deepcopy(db.record.get("inherited_semantic_findings")),
            "findings_note": "Inherited findings have historical/unknown applicability; neither they nor this context are a pass certificate.",
            "prior_work_units": (_schedule_view(db.record["prior_work_units"])
                                 if isinstance(db.record.get("prior_work_units"), dict)
                                 and db.record["prior_work_units"].get("units") else None),
            "schedule": _schedule_view(db.record["work_units"]) if _valid_schedule(db.record, project, db.source_id) else None,
            "omitted": "Other mechanisms remain stored but are omitted here. Current-unit full objects and current active mechanisms touched in completed units are supplied exactly, without raw argument replay or text truncation. Use manifest uses for scheduling and context; the compiler validates reference legality."}


def current_unit_context(db):
    project = db.get(db.project.id)
    objects = _objects(project)
    result = _initial_unit_context(db, project, objects)
    if not _valid_schedule(db.record, project, db.source_id):
        result["current_unit"] = None
        return result
    schedule = db.record["work_units"]
    pin = db.record.get("unit_request")
    unit = next((u for u in schedule["units"] if pin and u["id"] == pin.get("unit_id")), None) or _next(schedule)
    if unit:
        keys = {_key(r["kind"].removeprefix("remove_").removeprefix("retire_"), r["id"]) for r in unit["changes"]}
        keys |= {_key(use["kind"], use["id"]) for r in unit["changes"] for use in r["uses"]}
        result["current_unit"] = {**deepcopy(unit), "objects": {k: deepcopy(objects[k]) for k in sorted(keys) if k in objects},
                                  "instruction": "Submit exactly these record identities as one complete PlanDelta. Listed fields are intent/size hints: update any necessary canonical field on those records, including a mechanism contradicted by the new acceptance. Preserve unchanged fields. Uses guides ordering and context, not reference authorization; existing legal objects may be linked, while all references still require compiler validation. Do not add records assigned to later units. Never submit the whole manifest as a full plan."}
        result["completed_contract_mechanisms"] = [item for item in result["completed_contract_mechanisms"]
                                                  if _key("contract", item["key"]) not in keys]
        architecture = result["current_unit"]["objects"].get("architecture:architecture")
        if architecture is not None:
            nodes = {node["id"]: node for node in architecture["diagram"]["nodes"]}
            if all(all(nodes.get(c["id"], {}).get(k) == v for k, v in c.items())
                   for c in result["components"]):
                # The complete current architecture already contains these
                # exact node facts. Keep the directory without repeating them.
                result["components"] = [{"id": c["id"], "label": c["label"]} for c in result["components"]]
                result["components_note"] = "Complete current component facts are in current_unit.objects['architecture:architecture'].diagram.nodes, joined by id. Directory text is not a replacement for those exact nodes."
    else:
        result["current_unit"] = None
    result["completed_unit_facts"] = deepcopy(schedule["checkpoints"])
    return result


def prepare_unit_request(db):
    project = db.get(db.project.id)
    if not _valid_schedule(db.record, project, db.source_id):
        return initial_unit_context(db)
    unit = _next(db.record["work_units"])
    pin = ({"unit_id": unit["id"], "unit_hash": unit["hash"], "source_id": db.source_id,
            "revision": project.revision, "planning_fingerprint": _identity(project),
            "candidate_hash": candidate_hash(project), "accepted_delta_hash": None} if unit else None)
    record = {**db.record, "unit_request": pin}
    db.record = db.store.save(record)
    return current_unit_context(db)


def _delta_rows(delta):
    raw = delta.model_dump(mode="json", exclude_unset=True)
    rows = []
    for kind, (field, identity) in COLLECTIONS.items():
        for item in raw.get(field, []):
            value = {k: v for k, v in item.items() if k != identity}
            rows.append({"change_id": _key(kind, item[identity]), "fields": set(value),
                         "uses": _references(kind, value)})
    for kind, field in REMOVALS.items():
        rows.extend({"change_id": _key(kind, identity), "fields": set(), "uses": []} for identity in raw.get(field, []))
    for kind, field in (("relation", "relations"), ("remove_relation", "remove_relations")):
        rows.extend({"change_id": _key(kind, _json([e["source"], e["target"], e["label"]])),
                     "fields": set(e), "uses": _references(kind, e)} for e in raw.get(field, []))
    if "target" in raw:
        rows.append({"change_id": "target:target", "fields": {"target"}, "uses": []})
    metadata = {k: raw[k] for k in ARCH_FIELDS & raw.keys()}
    if metadata:
        rows.append({"change_id": "architecture:architecture", "fields": set(metadata), "uses": _references("architecture", metadata)})
    by_id = {r["change_id"]: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError("delta repeats a scheduled identity")
    for key in raw.get("restore_contract_keys", []):
        if _key("contract", key) not in by_id:
            raise ValueError("restoration requires a contract in the current unit")
        by_id[_key("contract", key)]["fields"].add("restore")
    return raw, by_id


def validate_unit_delta(db, args):
    from .plan_ir import PlanDelta

    project = db.get(db.project.id)
    args = PlanDelta.model_validate(args.model_dump(exclude_unset=True) if isinstance(args, PlanDelta) else args)
    raw, actual = _delta_rows(args)
    schedule, pin = db.record.get("work_units"), db.record.get("unit_request")
    if not schedule or not pin or pin.get("source_id") != db.source_id or schedule.get("source_id") != db.source_id:
        raise ValueError("schedule changes first; only the exact unit assigned before this provider request may be submitted")
    if pin.get("accepted_delta_hash"):
        if pin["accepted_delta_hash"] != _hash(raw) or not _valid_schedule(db.record, project, db.source_id):
            raise ValueError("this request already completed its unit; it cannot write the next unseen unit")
        return True
    if (pin["revision"] != project.revision or pin["planning_fingerprint"] != _identity(project)
            or not _valid_schedule(db.record, project, db.source_id)):
        raise ValueError("assigned unit is stale; do not overwrite a changed candidate")
    unit = next((u for u in schedule["units"] if u["id"] == pin["unit_id"]), None)
    if not unit or unit["state"] != "pending" or unit["hash"] != pin["unit_hash"]:
        raise ValueError("assigned unit is unavailable or held")
    expected = {r["change_id"]: r for r in unit["changes"]}
    if actual.keys() != expected.keys():
        raise ValueError("submit exactly current unit identities: " + ", ".join(expected))
    catalog = manifest_field_catalog()
    for key, row in actual.items():
        wanted = expected[key]
        kind = wanted["kind"]
        # Routing fields estimate work; they cannot lock an acceptance change
        # away from its own mechanism. The assigned record identity stays fixed.
        if row["fields"] - set(catalog[kind]["fields"]):
            raise ValueError("delta contains non-canonical fields: " + key)
        # References are planning data, not a separate authorization boundary.
        # The compiler checks them against the candidate plus this exact unit.


def advance_unit_checkpoint(record, old_project, new_project, compiler_audit):
    """Called inside the existing atomic save, and its NO_PROGRESS audit path."""
    result = deepcopy(record)
    schedule, pin = result.get("work_units"), result.get("unit_request")
    source_id = result.get("turn_id", result.get("id"))
    if not schedule or schedule.get("source_id") != source_id:
        return result
    if compiler_audit is None:
        if _valid_schedule(result, old_project, source_id) and _identity(old_project) == _identity(new_project):
            schedule.update(expected_revision=new_project.revision, expected_fingerprint=_identity(new_project))
        return result
    if not pin or pin.get("source_id") != source_id or not _valid_schedule(result, old_project, source_id):
        raise ValueError("no current server-assigned unit checkpoint")
    raw = compiler_audit.get("ir")
    # Recheck the actual compiler audit inside the transaction, not only the
    # earlier handler guard. No model-supplied completion flag can advance it.
    from types import SimpleNamespace
    proxy = SimpleNamespace(project=old_project, record=result, source_id=source_id,
                            get=lambda project_id: old_project)
    validate_unit_delta(proxy, raw)
    digest = _hash(raw)
    unit = next((u for u in schedule["units"] if u["id"] == pin["unit_id"]), None)
    if not unit or unit["hash"] != pin["unit_hash"]:
        raise ValueError("unit identity changed before checkpoint")
    if unit["state"] == "completed":
        if pin.get("accepted_delta_hash") != digest or _identity(old_project) != _identity(new_project):
            raise ValueError("completed unit replay must be exact and non-mutating")
        return result
    if pin["revision"] != old_project.revision or pin["planning_fingerprint"] != _identity(old_project):
        raise ValueError("unit checkpoint base is stale")
    if _identity(old_project) == _identity(new_project):
        raise ValueError("pending unit promised a change but produced NO_PROGRESS; it cannot be marked complete")
    unit["state"] = "completed"
    pin["accepted_delta_hash"] = digest
    schedule["completed_ids"].append(unit["id"])
    schedule["pending_ids"] = [key for key in schedule["pending_ids"] if key != unit["id"]]
    schedule.update(expected_revision=new_project.revision, expected_fingerprint=_identity(new_project))
    schedule["checkpoints"].append({"unit_id": unit["id"], "unit_hash": unit["hash"], "source_id": source_id,
        "before_revision": old_project.revision, "after_revision": new_project.revision,
        "candidate_hash": candidate_hash(new_project), "delta_hash": digest,
        "change_ids": [r["change_id"] for r in unit["changes"]],
        "submitted_fields": {key: sorted(row["fields"]) for key, row in _delta_rows(
            PlanDelta.model_validate(raw))[1].items()},
        "changed": _identity(old_project) != _identity(new_project)})
    # A successful compiler checkpoint resolves this unit's transport/schema
    # rejections only. Never clear semantic findings because a field was touched.
    findings = result.get("report", {}).get("findings", [])
    corrected = [f for f in findings if f.get("code") == "invalid_plan_delta"
                 and f.get("unit_id") == unit["id"] and f.get("source_id") == source_id]
    if corrected:
        result.setdefault("resolved_generation_findings", []).extend(
            {**f, "resolution": "complete_unit_checkpoint", "after_revision": new_project.revision,
             "delta_hash": digest} for f in corrected)
        result["report"]["findings"] = [f for f in findings if f not in corrected]
    return result


def all_units_complete(record):
    schedule = record.get("work_units")
    if not isinstance(schedule, dict) or not schedule.get("units") or not schedule.get("manifest"):
        return False
    try:
        project = Project.model_validate(record["project"])
        source_id = record.get("turn_id", record.get("id"))
        if not _valid_schedule(record, project, source_id):
            return False
        units = schedule["units"]
        ids = [u["id"] for u in units]
        changes = [r for u in units for r in u["changes"]]
        if (len(ids) != len(set(ids)) or schedule["pending_ids"]
                or schedule["completed_ids"] != ids
                or schedule["manifest_hash"] != _hash(schedule["manifest"])
                or sorted(r["change_id"] for r in changes) != sorted(r["change_id"] for r in schedule["manifest"])):
            return False
        return all(u["state"] == "completed" and u["hash"] == _hash(u["changes"])
                   and any(c.get("unit_id") == u["id"] and c.get("unit_hash") == u["hash"]
                           and c.get("delta_hash") and c.get("candidate_hash")
                           and c.get("change_ids") == [r["change_id"] for r in u["changes"]]
                           for c in schedule["checkpoints"]) for u in units)
    except (KeyError, TypeError, AttributeError, ValueError):
        return False


def resume_unit_schedule(previous, candidate, new_source_id, content):
    """Exact-input continuation only; keep original provenance and completed units."""
    old = previous.get("work_units")
    if not old or old.get("input_text") != content:
        return None
    original = candidate.model_copy(deep=True)
    additions = [s for s in original.plan_contract.sources if s.id == new_source_id]
    original.plan_contract.sources = [s for s in original.plan_contract.sources if s.id != new_source_id]
    origin = next((s for s in original.plan_contract.sources if s.id == old.get("origin_source_id")), None)
    if (old.get("project_id") != candidate.id or _identity(original) != old.get("expected_fingerprint")
            or len(additions) != 1 or additions[0].text != content or origin is None
            or additions[0].reference_context != origin.reference_context):
        return None
    result = deepcopy(old)
    for unit in result.get("units", []):
        changes = unit.get("changes", [])
        if (unit.get("state") == "held" and unit.get("holds") == [SIZE_HOLD]
                and len(changes) == 1 and changes[0] in result.get("manifest", [])
                and unit.get("hash") == _hash(changes)
                and unit.get("id") in result.get("pending_ids", [])
                and unit.get("id") not in result.get("completed_ids", [])
                and unit.get("estimated_text_bytes", 0) > MAX_UNIT_BYTES
                and unit.get("contract_count") == int(changes[0]["kind"] in {"contract", "remove_contract"})):
            # Reassess only this recognized legacy size hold. Never repack the
            # schedule or alter its identities, dependencies or checkpoints.
            result.setdefault("sizing_reassessments", []).append({
                "unit_id": unit["id"], "unit_hash": unit["hash"], "source_id": new_source_id,
                "previous_holds": list(unit["holds"]), "reason": "isolated_singleton_packing_target",
            })
            unit.update(state="pending", holds=[])
    result.setdefault("resumes", []).append({"source_id": new_source_id, "candidate_hash": candidate_hash(candidate)})
    result.update(source_id=new_source_id, expected_revision=candidate.revision,
                  expected_fingerprint=_identity(candidate))
    return result


MANIFEST_TOOL = ToolSpec(
    "schedule_plan_changes",
    "Schedule a SHORT manifest, not a full plan. Each change gives kind, stable id, changed field names, "
    "uses={field,kind,id} guides dependency ordering and read context, not reference authorization. "
    "uses may name unchanged reference fields for read context; fields lists only intended changes. "
    "Existing candidate objects may be linked; declare genuinely new targets for definition ordering. "
    "fields are intent/size hints, not a lock against necessary same-record repairs; intent <=120 chars. Never include statements, "
    "mechanisms, quotes or other field values. kind names match singular PlanDelta collections; remove_* "
    "kinds have fields=[]. target id=target and fields=[target]; architecture id=architecture names its "
    "changed global metadata fields. relation/remove_relation id is JSON [source,target,label], with "
    "fields=[source,target,label] and component uses for source and target. Contract owner uses slice; "
    "requirement_ids/component_ids/requires_behavior_keys use their singular kind; provides/steps use "
    "capability IDs (inspect steps use requirement). New IDs must include their first usable definition. "
    "The server targets <=3-contract/6-KiB-existing-text units, isolating indivisible singleton records "
    "and holding oversized multi-record atomic groups. Submit "
    "only the assigned unit in the NEXT model request; no read-scope loop. Existing compiler checks remain mandatory.",
    SchedulePlanChanges, schedule_plan_changes, "安排小型规划工作单元", "inspect", None,
)
