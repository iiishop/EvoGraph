"""Advertise the pinned unit through the existing tool, never replace admission.

Only operation identities are narrowed. Reference fields and same-record partial
updates retain the compiler's schema. Unsupported or untrusted projections use
the complete original schema with an audit reason; existing server admission,
atomic compilation, budgets and semantic gates remain authoritative.
"""
import json
from copy import deepcopy
from dataclasses import replace

from .plan_ir import DELTA_TOOL, PlanDelta, required_plan_fields
from .plan_units import (
    ARCH_FIELDS,
    BOOTSTRAP_FIELDS,
    COLLECTIONS,
    FIELD_UNIT_VERSIONS,
    REMOVALS,
    _identity,
    _key,
    _kind_fields,
    _next,
    _objects,
    schedule_findings,
)


def _object_items(schema, field):
    items = schema["properties"][field]["items"]
    ref = items.get("$ref", "")
    if not ref.startswith("#/$defs/") or set(items) != {"$ref"}:
        raise ValueError("unsupported collection schema: " + field)
    result = deepcopy(schema["$defs"][ref.removeprefix("#/$defs/")])
    if result.get("type") != "object" or result.get("additionalProperties") is not False:
        raise ValueError("unsupported item schema: " + field)
    return result


def _prune_definitions(schema):
    def references(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "$ref":
                    if not isinstance(child, str) or not child.startswith("#/$defs/"):
                        raise ValueError("unsupported schema reference")
                    yield child.removeprefix("#/$defs/")
                else:
                    yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)

    definitions = schema.pop("$defs", {})
    used, pending = set(), set(references(schema))
    while pending:
        name = pending.pop()
        if name in used:
            continue
        used.add(name)
        pending.update(set(references(definitions[name])) - used)
    if used:
        schema["$defs"] = {name: value for name, value in definitions.items() if name in used}


def project_unit_schema(record, project):
    """Pure projection plus explicit coverage/fallback diagnostics for offline replay."""
    original = DELTA_TOOL.parameters.model_json_schema()
    pin = record.get("unit_request") or {}
    audit = {"version": "unit-tool-schema/v1", "unit_id": pin.get("unit_id"),
             "unit_hash": pin.get("unit_hash"), "manifest_hash": pin.get("manifest_hash"),
             "status": "fallback_full_schema", "reason": None, "covered_change_ids": [],
             "coverage": "Operation identities only; ordinary references and partial fields stay unchanged. "
                         "Server exact-identity, compiler and semantic checks remain mandatory."}
    try:
        schedule = record.get("work_units")
        if not schedule or not pin:
            raise ValueError("missing schedule or unit request")
        findings = schedule_findings(record, project)
        if findings:
            raise ValueError("invalid schedule: " + "; ".join(f["message"] for f in findings))
        unit = _next(schedule)
        if (unit is None or pin.get("unit_id") != unit["id"] or pin.get("unit_hash") != unit["hash"]
                or pin.get("source_id") != schedule["source_id"]
                or pin.get("manifest_hash") != schedule["manifest_hash"]
                or pin.get("revision") != project.revision
                or pin.get("planning_fingerprint") != _identity(project)
                or pin.get("accepted_delta_hash")):
            raise ValueError("missing, stale or completed unit pin")
        schema = deepcopy(original)
        properties = schema["properties"]
        extras = {name: field.json_schema_extra["manifest_extra"]
                  for name, field in PlanDelta.model_fields.items()
                  if (field.json_schema_extra or {}).get("manifest_extra")}
        known = {"summary", "target", "relations", "remove_relations", *ARCH_FIELDS, *extras,
                 *REMOVALS.values(), *(field for field, _ in COLLECTIONS.values())}
        if properties.keys() != known:
            raise ValueError("uncataloged PlanDelta operation fields")
        keep, required, covered = {"summary"}, set(), []
        objects = _objects(project)
        by_kind = {}
        for row in unit["changes"]:
            by_kind.setdefault(row["kind"], []).append(row)
        for kind, rows in by_kind.items():
            ids = [row["id"] for row in rows]
            if kind in COLLECTIONS:
                field, identity = COLLECTIONS[kind]
                model, _ = _kind_fields(kind)
                base = _object_items(schema, field)
                branches = []
                # Two disjoint identity enums preserve creation requirements
                # without forcing complete rewrites of existing records.
                for new in (False, True):
                    selected = [value for value in ids if (_key(kind, value) not in objects) == new]
                    if not selected:
                        continue
                    branch = deepcopy(base)
                    branch["properties"][identity]["enum"] = selected
                    branch["required"] = sorted(required_plan_fields(model, new=new))
                    branches.append(branch)
                properties[field]["items"] = branches[0] if len(branches) == 1 else {"anyOf": branches}
            elif kind in REMOVALS:
                field = REMOVALS[kind]
                properties[field]["items"]["enum"] = ids
            elif kind in {"relation", "remove_relation"}:
                field = "relations" if kind == "relation" else "remove_relations"
                base = _object_items(schema, field)
                branches = []
                for identity in ids:
                    branch = deepcopy(base)
                    # One branch per exact triple avoids the cross-product
                    # admitted by independent source/target/label enums.
                    for name, value in zip(("source", "target", "label"), json.loads(identity), strict=True):
                        branch["properties"][name]["enum"] = [value]
                    branches.append(branch)
                properties[field]["items"] = branches[0] if len(branches) == 1 else {"anyOf": branches}
            elif kind == "target":
                keep.add("target")
                required.add("target")
                covered.extend(row["change_id"] for row in rows)
                continue
            elif kind == "architecture":
                fields = {f for row in rows for f in row["fields"]}
                # v1 does not enforce metadata field partitions. Never turn
                # its routing hints into additional write restrictions.
                keep.update(fields if schedule["version"] in FIELD_UNIT_VERSIONS else ARCH_FIELDS)
                if schedule["version"] in FIELD_UNIT_VERSIONS:
                    required.update(fields)
                if not project.architectures:
                    required.update(BOOTSTRAP_FIELDS)
                covered.extend(row["change_id"] for row in rows)
                continue
            else:
                raise ValueError("unsupported operation family: " + kind)
            if len(ids) > properties[field]["maxItems"]:
                raise ValueError("unit exceeds original collection limit: " + field)
            properties[field].update(minItems=len(ids), maxItems=len(ids))
            keep.add(field)
            required.add(field)
            covered.extend(row["change_id"] for row in rows)
        for field, extra in extras.items():
            # Catalogued acknowledgments (currently restoration) attach to
            # submitted identities. Do not limit them to router field hints.
            if extra["kind"] not in by_kind:
                continue
            if properties[field].get("items", {}).get("type") != "string":
                raise ValueError("unsupported manifest acknowledgment: " + field)
            keep.add(field)
            ids = [row["id"] for row in by_kind[extra["kind"]]]
            properties[field]["items"]["enum"] = ids
            properties[field]["maxItems"] = min(len(ids), properties[field]["maxItems"])
        schema["properties"] = {name: value for name, value in properties.items() if name in keep}
        if keep - schema["properties"].keys():
            raise ValueError("missing operation schema")
        if required:
            schema["required"] = sorted(required)
        else:
            schema.pop("required", None)
        _prune_definitions(schema)
        audit.update(status="projected", covered_change_ids=covered,
                     operation_fields=sorted(keep - {"summary"}))
        return schema, audit
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        audit["reason"] = str(error)
        # All-or-nothing: a partial projection could silently hide real intent.
        return original, audit


def unit_delta_tool_for(db):
    schema, audit = project_unit_schema(db.record, db.get(db.project.id))
    db.record = db.store.save({**db.record, "tool_schema_projections": [
        *db.record.get("tool_schema_projections", []), audit]})
    return replace(DELTA_TOOL, parameters_schema=schema)
