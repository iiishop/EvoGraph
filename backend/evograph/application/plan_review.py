"""Batch review transport and replayable, candidate-bound review certificates."""

import hashlib
import json
import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.dependencies import ancestor_sets
from ..domain.models import Project
from ..domain.plan_contracts import (
    SemanticCheck,
    SemanticReview,
    active_behaviors,
    candidate_hash,
    eligible_execution_evidence,
    planning_fingerprint,
    review_subjects,
    validate_semantic_review,
)
from ..domain.typed_capabilities import (
    capability_changes,
    capability_providers,
    declared_consumption_keys,
)
from .plan_patch import contract_changes

CHECKER_VERSION = "unified-contract-challenge-v12"
BATCH_VERSION = "semantic-batch/v2"
_ALIAS_KEY = "$packet_ref"
_RECORD_LISTS = (
    ("milestones",), ("behaviors",), ("source_milestones",), ("research",),
    ("plan_contract", "sources"), ("plan_contract", "requirements"),
    ("plan_contract", "bindings"), ("plan_contract", "process_constraints"),
    ("architecture", "quality_scenarios"), ("architecture", "technologies"),
    ("architecture", "diagram", "nodes"), ("architecture", "diagram", "edges"),
    ("architecture", "diagram", "groups"),
)
_RECORD_FIELDS = (("behaviors", "statement"), ("plan_contract", "bindings", "mechanism"))
_LEGACY_RECORD_ENCODING = {
    "version": "before-record-aliases/v1", "alias_key": _ALIAS_KEY,
    "meaning": "A before-record alias contains exactly the full candidate record at its local pointer. "
               "Keep before/candidate identity when citing evidence. Expand aliases before comparing "
               "facts; shared bytes do not establish semantic support.",
}
_RECORD_ENCODING = {
    "version": "before-record-aliases/v2", "alias_key": _ALIAS_KEY,
    "meaning": "A before alias contains exactly the full candidate record, or the complete "
               "statement/mechanism string for the same behavior_key, at its local pointer. "
               "Keep before/candidate identity when citing evidence. Expand aliases before comparing "
               "facts; shared bytes do not establish semantic support.",
}
_TEXT_KEY = "$t"
_TEXT_ENCODING = {
    "version": "typed-string-pool/v1", "ref": _TEXT_KEY, "minimum_utf8_bytes": 32,
    "meaning": "Schema-declared before/candidate string leaves and current_input only: {$t:i} "
               "is the complete raw text_table[i]. Decode text before before-record aliases. "
               "Source identities and paths stay distinct; shared text is not semantic proof.",
}
_CATALOGUE_ENCODING = {
    "version": "ordered-pointer-runs/v1",
    "meaning": "[prefix,n] expands to prefix/0 through prefix/(n-1), in order; strings are "
               "exact pointers. Cite expanded pointers.",
}
REVIEW_PACKET_INSTRUCTIONS = """Transport: {$t:i} at a declared snapshot/current_input string leaf
means the complete literal text_table[i], never another reference. Decode text before the one-way
before-to-candidate record aliases. In evidence_catalog, [prefix,n] means prefix/0 through prefix/(n-1).
Cite those expanded exact pointers, retaining before/candidate identity. Sharing is not semantic proof.
"""
_PROJECT_SCHEMA = Project.model_json_schema()
_SNAPSHOT_FIELDS = {
    "target": ("targets", "0"), "target_draft": ("target_draft",),
    "milestones": ("milestones",), "behaviors": ("behaviors",),
    "architecture": ("architectures", "0"), "source_milestones": ("source_milestones",),
    "plan_contract": ("plan_contract",), "research": ("research",),
}


class BatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def nonblank_text(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("评审字段不能只有空白")
        return value


class BatchStatuses(BatchModel):
    supported: list[str] = Field(max_length=300)
    contradicted: list[str] = Field(max_length=300)
    unknown: list[str] = Field(max_length=300)


class MaterialityEvidence(BatchModel):
    """Bounded provenance and a model's causal claim, never an entailment proof."""
    obligation_ref: str = Field(min_length=1, max_length=240)
    obligation_excerpt: str = Field(min_length=1, max_length=320)
    affected_owner_ids: list[str] = Field(max_length=64)
    gap_kind: Literal["explicit_conflict", "unresolved_semantics", "unavailable_prerequisite"]
    boundary_refs: list[str] = Field(min_length=1, max_length=8)
    necessary_plan_change: str = Field(min_length=1, max_length=320)
    # reason/counterexample below explain why ordinary owned implementation
    # cannot fulfill the unchanged plan. Do not duplicate that prose here.
    consumer_owner_id: str | None = Field(default=None, max_length=120)
    provider_ref: str | None = Field(default=None, max_length=240)


class BatchIssue(BatchModel):
    id: str = Field(min_length=1, max_length=40)
    subjects: list[str] = Field(min_length=1, max_length=300)
    materiality: MaterialityEvidence
    verdict: Literal["contradicted", "unknown"]
    reason: str = Field(min_length=1, max_length=240)
    counterexample: str = Field(min_length=1, max_length=320)
    basis: Literal["model_inference", "source_statement", "existing_execution_record"] = "model_inference"
    execution_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class BatchObservation(BatchModel):
    """Advisory audit data; cannot cover an unknown/contradicted subject."""
    id: str = Field(min_length=1, max_length=40)
    subjects: list[str] = Field(min_length=1, max_length=300)
    kind: Literal["editorial", "implementation_latitude"]
    reason: str = Field(min_length=1, max_length=320)
    basis: Literal["model_inference", "source_statement", "existing_execution_record"] = "model_inference"
    execution_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class BatchSemanticReview(BatchModel):
    candidate_hash: str
    review_scope_hash: str
    summary: str = Field(min_length=1, max_length=500)
    statuses: BatchStatuses
    issues: list[BatchIssue] = Field(max_length=300)
    observations: list[BatchObservation] = Field(max_length=100)


def _pointer_tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise ValueError("评审依据必须是包内具体 JSON pointer")
    tokens = pointer[1:].split("/")
    if any(re.search(r"~(?![01])", token) for token in tokens):
        raise ValueError("评审依据含非法 JSON pointer 转义")
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in tokens)


def _pointer_child(value, token):
    try:
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                raise ValueError
            return value[int(token)]
        if not isinstance(value, dict):
            raise TypeError
        return value[token]
    except (KeyError, IndexError, ValueError, TypeError):
        raise ValueError("评审依据指向不存在的包内记录") from None


def _record_group(tokens):
    if not tokens or tokens[0] not in {"before", "candidate"}:
        return None
    path = tokens[1:]
    if path in {("target",), ("architecture",)}:
        return path
    if path[:-1] in _RECORD_LISTS and re.fullmatch(r"0|[1-9][0-9]*", path[-1]):
        return path[:-1]
    field_group = (*path[:-2], *path[-1:])
    if field_group in _RECORD_FIELDS and re.fullmatch(r"0|[1-9][0-9]*", path[-2]):
        return field_group
    return None


def _string_schema_position(schema, tokens):
    if "$ref" in schema:
        schema = _PROJECT_SCHEMA["$defs"][schema["$ref"].rsplit("/", 1)[1]]
    for union in ("anyOf", "oneOf"):
        if union in schema:
            return any(_string_schema_position(item, tokens) for item in schema[union])
    if not tokens:
        return schema.get("type") == "string"
    token, *rest = tokens
    if schema.get("type") == "array":
        return (bool(re.fullmatch(r"0|[1-9][0-9]*", token))
                and _string_schema_position(schema["items"], rest))
    if schema.get("type") == "object":
        child = schema.get("properties", {}).get(token, schema.get("additionalProperties"))
        # An untyped dict (source activity/evidence/context) is opaque data.
        return isinstance(child, dict) and _string_schema_position(child, rest)
    return False


def _text_position(location):
    if location == ("current_input",):
        return True
    if (len(location) < 2 or location[0] not in {"before", "candidate"}
            or location[1] not in _SNAPSHOT_FIELDS):
        return False
    return _string_schema_position(
        _PROJECT_SCHEMA, (*_SNAPSHOT_FIELDS[location[1]], *location[2:]))


def _decode_packet_text(packet):
    if "text_encoding" not in packet and "text_table" not in packet:
        return packet
    table = packet.get("text_table")
    if (packet.get("text_encoding") != _TEXT_ENCODING or not isinstance(table, list)
            or any(not isinstance(item, str) or len(item.encode()) < 32 for item in table)
            or table != sorted(set(table))):
        raise ValueError("评审文本池版本或完整字符串表非法")

    def decode(value, location):
        if _text_position(location) and isinstance(value, dict):
            if _ALIAS_KEY in value and _record_group(location) in _RECORD_FIELDS:
                # This distinct first-layer reference is checked by the record
                # resolver below, including identity, physical path and cycles.
                return value
            index = value.get(_TEXT_KEY)
            if set(value) != {_TEXT_KEY} or type(index) is not int or not 0 <= index < len(table):
                raise ValueError("评审文本引用必须是有效的终止字符串索引")
            return table[index]
        if isinstance(value, dict):
            return {key: decode(item, (*location, key)) for key, item in value.items()}
        if isinstance(value, list):
            return [decode(item, (*location, str(i))) for i, item in enumerate(value)]
        return value

    return {key: decode(value, (key,)) for key, value in packet.items()
            if key not in {"text_encoding", "text_table"}}


def resolve_packet_pointer(packet, pointer):
    """Resolve evidence paths, expanding only explicitly allowed before aliases.

    The terminal typed text layer is decoded first. One-way, same-kind record
    links still forbid chains/cycles; arbitrary source JSON is never executable.
    """
    packet = _decode_packet_text(packet)
    def dereference(value, location):
        if _text_position(location) and isinstance(value, dict) and _TEXT_KEY in value:
            raise ValueError("评审文本引用缺少有效的文本池编码")
        group = _record_group(location)
        if group is None or not isinstance(value, dict) or _ALIAS_KEY not in value:
            return value, location
        encoding = packet.get("record_encoding")
        valid_encoding = (encoding == _RECORD_ENCODING
                          or (group not in _RECORD_FIELDS and encoding == _LEGACY_RECORD_ENCODING))
        if (location[0] != "before" or set(value) != {_ALIAS_KEY}
                or not valid_encoding):
            raise ValueError("评审记录别名格式或位置非法")
        target = _pointer_tokens(value[_ALIAS_KEY])
        if target[0] != "candidate" or _record_group(target) != group:
            raise ValueError("评审别名必须指向候选中的同类完整记录或字段")
        result = packet
        for token in target:
            result = _pointer_child(result, token)
        if group in _RECORD_FIELDS:
            before_record, candidate_record = packet, packet
            for token in location[:-1]:
                before_record = _pointer_child(before_record, token)
            for token in target[:-1]:
                candidate_record = _pointer_child(candidate_record, token)
            key = before_record.get("behavior_key")
            if (not isinstance(result, str) or not isinstance(key, str) or not key
                    or candidate_record.get("behavior_key") != key):
                raise ValueError("评审字段别名必须指向同一行为身份的完整同类字符串")
        elif not isinstance(result, dict) or _ALIAS_KEY in result:
            raise ValueError("评审记录别名不能链接别名或非记录值")
        return result, target

    def expand(value, location):
        value, location = dereference(value, location)
        if isinstance(value, dict):
            return {key: expand(item, (*location, key)) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item, (*location, str(i))) for i, item in enumerate(value)]
        return value

    value, location = packet, ()
    for token in _pointer_tokens(pointer):
        # Keep the physical origin after a link: a candidate descendant must
        # never acquire permission to act as another before-record alias.
        value, location = dereference(value, location)
        value = _pointer_child(value, token)
        location = (*location, token)
    return expand(value, location)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pool_review_text(packet):
    """Share only equal complete typed strings, with canonical table ordering."""
    original = _json(packet)
    counts = Counter()

    def visit(value, location=(), indices=None):
        if _text_position(location) and isinstance(value, str):
            if indices is not None:
                return {_TEXT_KEY: indices[value]} if value in indices else value
            if len(value.encode()) >= 32:
                counts[value] += 1
        if isinstance(value, dict):
            return {key: visit(item, (*location, key), indices) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item, (*location, str(i)), indices) for i, item in enumerate(value)]
        return value

    visit(packet)
    table = sorted(value for value, count in counts.items() if count > 1)
    encoded = visit(packet, indices={value: i for i, value in enumerate(table)})
    encoded["text_table"] = table
    encoded["text_encoding"] = dict(_TEXT_ENCODING)
    if _json(_decode_packet_text(encoded)) != original:
        raise ValueError("评审文本共享未完整保留原始包")
    packet.clear()
    packet.update(encoded)


def _compact_catalogue(refs):
    result, index = [], 0
    while index < len(refs):
        match = re.fullmatch(r"(.+)/0", refs[index])
        end = index + 1
        if match:
            while end < len(refs) and refs[end] == f"{match[1]}/{end - index}":
                end += 1
        if end - index >= 2:
            result.append([match[1], end - index])
        else:
            result.append(refs[index])
        index = end
    return result


def _expanded_evidence_catalog(packet):
    catalog = packet.get("evidence_catalog")
    encoding = packet.get("evidence_catalog_encoding")
    if not isinstance(catalog, list) or (encoding is not None and encoding != _CATALOGUE_ENCODING):
        raise ValueError("评审依据目录编码非法")
    decoded, refs = _decode_packet_text(packet), []
    for item in catalog:
        if isinstance(item, str):
            _pointer_tokens(item)
            refs.append(item)
        elif (encoding == _CATALOGUE_ENCODING and isinstance(item, list) and len(item) == 2
              and isinstance(item[0], str) and type(item[1]) is int and item[1] >= 2):
            records = resolve_packet_pointer(decoded, item[0])
            if not isinstance(records, list) or len(records) != item[1]:
                raise ValueError("评审依据目录范围不对应完整包内列表")
            refs.extend(f"{item[0]}/{i}" for i in range(item[1]))
        else:
            raise ValueError("评审依据目录项非法")
    if len(refs) != len(set(refs)) or (encoding is not None and _compact_catalogue(refs) != catalog):
        raise ValueError("评审依据目录重复或不是规范编码")
    for pointer in refs:
        resolve_packet_pointer(decoded, pointer)
    return refs


def _deduplicate_before_records(packet):
    """Intern equal records and two named fields without dropping any content."""
    original = _json(packet["before"])
    packet["record_encoding"] = dict(_RECORD_ENCODING)
    for group in (("target",), ("architecture",), *_RECORD_LISTS):
        records = {}
        for side in ("candidate", "before"):
            if group[0] == "architecture" and len(group) > 1:
                architecture = packet[side]["architecture"]
                if architecture is None or (side == "before" and _ALIAS_KEY in architecture):
                    continue
            parent = packet[side]
            for field in group[:-1]:
                parent = parent[field]
            value = parent[group[-1]]
            entries = enumerate(value) if group in _RECORD_LISTS else [(group[-1], value)]
            container = value if group in _RECORD_LISTS else parent
            for key, record in entries:
                if not isinstance(record, dict):
                    continue
                encoded = _json(record)
                path = (side, *group, str(key)) if group in _RECORD_LISTS else (side, *group)
                if side == "candidate":
                    records.setdefault(encoded, "/" + "/".join(path))
                elif encoded in records:
                    alias = {_ALIAS_KEY: records[encoded]}
                    if len(_json(alias).encode()) < len(encoded.encode()):
                        container[key] = alias
    for group in _RECORD_FIELDS:
        before_records, candidate_records = packet["before"], packet["candidate"]
        for field in group[:-1]:
            before_records, candidate_records = before_records[field], candidate_records[field]
        field = group[-1]
        candidates = {item["behavior_key"]: (i, item) for i, item in enumerate(candidate_records)}
        for record in before_records:
            if _ALIAS_KEY in record:
                continue
            match = candidates.get(record["behavior_key"])
            if match is None:
                continue
            index, candidate_record = match
            value = record[field]
            if isinstance(value, str) and value == candidate_record[field]:
                pointer = "/" + "/".join(("candidate", *group[:-1], str(index), field))
                alias = {_ALIAS_KEY: pointer}
                if len(_json(alias).encode()) < len(_json(value).encode()):
                    record[field] = alias
    if _json(resolve_packet_pointer(packet, "/before")) != original:
        raise ValueError("评审记录去重未完整保留原始快照")


def _snapshot(project):
    active = {bid for milestone in project.milestones for bid in milestone.behavior_revision_ids}
    return {
        "target": project.targets[-1].model_dump() if project.targets else None,
        "target_draft": project.target_draft,
        "milestones": [m.model_dump() for m in project.milestones],
        "behaviors": [b.model_dump() for b in project.behaviors if b.id in active],
        "architecture": project.architectures[-1].model_dump() if project.architectures else None,
        "source_milestones": [m.model_dump() for m in project.source_milestones],
        "plan_contract": project.plan_contract.model_dump(),
        "research": [r.model_dump() for r in project.research],
    }


def _record_refs(packet, path, identity):
    records = resolve_packet_pointer(packet, path)
    refs = {item[identity]: f"{path}/{index}" for index, item in enumerate(records)}
    if len(refs) != len(records):
        raise ValueError("评审输入含重复记录身份")
    return refs


def _delta_refs(before, candidate, packet):
    refs = {side: {
        "behavior": _record_refs(packet, f"/{side}/behaviors", "behavior_key"),
        "binding": _record_refs(packet, f"/{side}/plan_contract/bindings", "behavior_key"),
    } for side in ("before", "candidate")}
    result = []
    for change in contract_changes(before, candidate):
        row = {key: value for key, value in change.items()
               if key not in {"fields", "before_owner", "after_owner"}}
        row["changed_fields"] = list(change["fields"])
        for side, old_name in (("before", "before"), ("candidate", "after")):
            present = change[f"{old_name}_revision_id"] is not None
            side_refs = {kind: mapping.get(change["key"]) if present else None
                         for kind, mapping in refs[side].items()}
            if present and side_refs["behavior"] is None:
                raise ValueError("契约差异缺少对应行为记录")
            for field, values in change["fields"].items():
                kind = "behavior" if field in {"owner", "statement", "acceptance_scope"} else "binding"
                pointer = side_refs[kind]
                actual = resolve_packet_pointer(packet, pointer).get(field) if pointer else None
                if actual != values[old_name]:
                    raise ValueError("契约差异与包内依据不一致")
            row[f"{old_name}_refs"] = side_refs if present else None
        result.append(row)
    return result


def _capability_delta_refs(before, candidate, packet):
    refs = {side: {
        "behavior": _record_refs(packet, f"/{side}/behaviors", "behavior_key"),
        "binding": _record_refs(packet, f"/{side}/plan_contract/bindings", "behavior_key"),
    } for side in ("before", "candidate")}

    def records(side, key):
        behavior_ref, binding_ref = (refs[side][kind].get(key) for kind in ("behavior", "binding"))
        if behavior_ref is None or binding_ref is None:
            raise ValueError("能力差异缺少对应行为或绑定记录")
        behavior = resolve_packet_pointer(packet, behavior_ref)
        binding = resolve_packet_pointer(packet, binding_ref)
        if binding["behavior_revision_id"] != behavior["id"]:
            raise ValueError("能力差异的行为与绑定版本不一致")
        identity = {"behavior_key": behavior["behavior_key"],
                    "behavior_revision_id": behavior["id"], "owner": behavior["owner"]}
        return behavior_ref, binding_ref, binding, identity

    result = []
    for change in capability_changes(before, candidate):
        row = {"key": change["key"]}
        for side, name in (("before", "before"), ("candidate", "after")):
            providers, consumers, used = [], [], set()
            for provider in change[f"{name}_providers"]:
                behavior_ref, binding_ref, binding, identity = records(side, provider["behavior_key"])
                matches = [i for i, provided in enumerate(binding["provides"])
                           if provided["key"] == change["key"]
                           and {**identity, "kind": provided["kind"], "action": provided["action"],
                                "consumes": provided.get("consumes")} == provider
                           and (binding_ref, i) not in used]
                if not matches:
                    raise ValueError("能力提供者差异与包内完整记录不一致")
                index = matches[0]
                used.add((binding_ref, index))
                providers.append({"behavior_ref": behavior_ref,
                                  "capability_ref": f"{binding_ref}/provides/{index}"})
            for consumer in change[f"{name}_consumers"]:
                behavior_ref, binding_ref, binding, identity = records(side, consumer["behavior_key"])
                indices = [i for i, step in enumerate(binding["steps"] or [])
                           if step["kind"] != "inspect" and step["capability_key"] == change["key"]]
                consumption = [[i, j] for i, provided in enumerate(binding["provides"])
                               for j, key in enumerate(provided.get("consumes") or [])
                               if key == change["key"]]
                if (not (indices or consumption)
                        or {**identity, "step_indices": indices,
                            "consumption_indices": consumption} != consumer):
                    raise ValueError("能力消费者差异与包内完整声明不一致")
                consumers.append({"behavior_ref": behavior_ref,
                                  "step_refs": [f"{binding_ref}/steps/{i}" for i in indices],
                                  "consumption_refs": [f"{binding_ref}/provides/{i}/consumes/{j}"
                                                       for i, j in consumption]})
            row[f"{name}_providers"] = providers
            row[f"{name}_consumers"] = consumers
        result.append(row)
    return result


def _capability_coverage(before, candidate, record, packet):
    """Reference-only declaration coverage, never a completeness certificate."""
    binding_refs = _record_refs(packet, "/candidate/plan_contract/bindings", "behavior_key")
    behavior_refs = _record_refs(packet, "/candidate/behaviors", "behavior_key")
    bindings = {binding.behavior_key: binding for binding in candidate.plan_contract.bindings}
    unmodeled, consumption, empty = [], [], []
    for key in active_behaviors(candidate):
        binding = bindings.get(key)
        absent = [field for field in ("provides", "steps")
                  if binding is None or not getattr(binding, field)]
        if absent:
            unmodeled.append({"behavior_ref": behavior_refs[key],
                              "binding_ref": binding_refs.get(key), "unmodeled_fields": absent})
        if binding is not None:
            for index, capability in enumerate(binding.provides):
                ref = f"{binding_refs[key]}/provides/{index}"
                if capability.consumes is None:
                    consumption.append(ref)
                elif not capability.consumes:
                    empty.append(ref)
    obligations = (declared_consumption_keys(before)
                   | {tuple(key) for key in record.get("consumption_obligation_keys", [])})
    current_keys = capability_providers(candidate)
    removed = [{"behavior_key": behavior_key, "capability_key": key}
               for behavior_key, key in sorted(obligations)
               if behavior_key in active_behaviors(candidate) and key not in current_keys]
    return {"scope": "Declared shared/public surface only; removed declarations are not fulfilled "
                     "obligations and require change/source review; omitted declarations are unmodeled, "
                     "and explicit [] does not prove mechanism completeness or semantic equivalence.",
            "unmodeled_acceptance": unmodeled, "unmodeled_consumption_refs": consumption,
            "declared_empty_consumption_refs": empty, "removed_consumption_declarations": removed}


def _slice_availability(candidate, packet):
    nodes = [*candidate.source_milestones, *candidate.milestones]
    graph = {m.id: list(m.dependencies) for m in nodes}
    if len(graph) != len(nodes):
        raise ValueError("评审交付图含重复切片身份")
    ancestors = ancestor_sets(graph)
    planned = {m.id: m for m in candidate.milestones}
    sources = {m.id: m for m in candidate.source_milestones}
    behavior_refs = _record_refs(packet, "/candidate/behaviors", "id")
    source_refs = _record_refs(packet, "/candidate/source_milestones", "id")
    rows = []
    for index, milestone in enumerate(candidate.milestones):
        available = {milestone.id, *ancestors[milestone.id]}
        available_ids = {bid for mid in available & planned.keys()
                         for bid in planned[mid].behavior_revision_ids}
        if available_ids - behavior_refs.keys():
            raise ValueError("评审交付图引用不存在的行为版本")
        rows.append({
            "subject": "slice_activation:" + milestone.id,
            "owner_ref": f"/candidate/milestones/{index}",
            "owned_behavior_refs": [behavior_refs[bid] for bid in milestone.behavior_revision_ids],
            "available_planned_slice_ids": sorted(available & planned.keys()),
            "available_behavior_keys": [b.behavior_key for b in candidate.behaviors if b.id in available_ids],
            "source_prerequisites": [{
                "id": mid, "milestone_ref": source_refs[mid],
                "capability_refs": [f"{source_refs[mid]}/source_behaviors/{i}"
                                    for i in range(len(sources[mid].source_behaviors))],
            } for mid in sorted(available & sources.keys())],
        })
    return rows


def _evidence_catalog(packet):
    refs = ["/current_input"]
    for side in ("before", "candidate"):
        data = packet[side]
        for field in ("target", "target_draft"):
            if data[field] is not None:
                refs.append(f"/{side}/{field}")
        paths = [f"/{side}/{name}" for name in ("milestones", "behaviors", "source_milestones", "research")]
        paths += [f"/{side}/plan_contract/{name}" for name in ("sources", "requirements", "bindings", "process_constraints")]
        for path in paths:
            refs.extend(f"{path}/{i}" for i in range(len(resolve_packet_pointer(packet, path))))
        for index, binding in enumerate(data["plan_contract"]["bindings"]):
            for field in ("provides", "steps"):
                refs.extend(f"/{side}/plan_contract/bindings/{index}/{field}/{i}"
                            for i in range(len(binding.get(field) or [])))
        for index, source in enumerate(data["source_milestones"]):
            refs.extend(f"/{side}/source_milestones/{index}/source_behaviors/{i}"
                        for i in range(len(source["source_behaviors"])))
        architecture = data["architecture"]
        if architecture:
            for field, value in architecture.items():
                if field == "diagram":
                    for part, records in value.items():
                        if isinstance(records, list):
                            refs.extend(f"/{side}/architecture/diagram/{part}/{i}" for i in range(len(records)))
                elif isinstance(value, list):
                    refs.extend(f"/{side}/architecture/{field}/{i}" for i in range(len(value)))
                else:
                    refs.append(f"/{side}/architecture/{field}")
    refs.extend(f"/available_execution_evidence/{i}" for i in range(len(packet["available_execution_evidence"])))
    refs.extend(f"/capability_delta/changes/{i}"
                for i in range(len(packet.get("capability_delta", {}).get("changes", []))))
    refs.extend(f"/capability_move_audits/{i}"
                for i in range(len(packet.get("capability_move_audits", []))))
    for name in ("references", "attachments"):
        records = packet["reference_context"].get(name, [])
        if isinstance(records, list):
            refs.extend(f"/reference_context/{name}/{i}" for i in range(len(records)))
    for pointer in refs:
        resolve_packet_pointer(packet, pointer)
    return refs


def batch_review_packet(before, candidate, record):
    packet = {
        "candidate_id": record["id"], "candidate_hash": candidate_hash(candidate),
        "planning_fingerprint": planning_fingerprint(candidate),
        "base_revision": record["base_revision"], "candidate_revision": candidate.revision,
        "snapshot_base_revision": before.revision,
        "checker_version": CHECKER_VERSION, "review_protocol": BATCH_VERSION,
        "baseline": candidate.baseline.model_dump() if candidate.baseline else None,
        "current_input": record["input"], "reference_context": record.get("reference_context", {}),
        # Preserve historical model explanations and their original versions;
        # they are neither current validation nor user authorization.
        "capability_move_audits": record.get("capability_move_audits", []),
        "before": _snapshot(before), "candidate": _snapshot(candidate),
        "required_subjects": review_subjects(candidate),
        "available_execution_evidence": eligible_execution_evidence(candidate), "complete": True,
    }
    packet["contract_delta"] = {
        "schema_version": "contract-delta-refs/v2", "project_id": candidate.id,
        "base_revision": before.revision, "candidate_revision": candidate.revision,
        "base_candidate_hash": candidate_hash(before), "candidate_hash": candidate_hash(candidate),
        "changes": _delta_refs(before, candidate, packet),
    }
    packet["capability_delta"] = {
        "schema_version": "capability-delta-refs/v2",
        "changes": _capability_delta_refs(before, candidate, packet),
    }
    packet["capability_coverage"] = _capability_coverage(before, candidate, record, packet)
    packet["slice_availability"] = _slice_availability(candidate, packet)
    packet["evidence_catalog"] = _evidence_catalog(packet)
    _deduplicate_before_records(packet)
    _pool_review_text(packet)
    refs = packet["evidence_catalog"]
    packet["evidence_catalog"] = _compact_catalogue(refs)
    packet["evidence_catalog_encoding"] = dict(_CATALOGUE_ENCODING)
    if _expanded_evidence_catalog(packet) != refs:
        raise ValueError("评审依据目录未完整保留原始指针")
    # Bind all review inputs, including execution evidence/baseline and source
    # context that the planning-only candidate hash intentionally does not cover.
    packet["review_scope_hash"] = hashlib.sha256(json.dumps(
        packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    return packet


def _checked_refs(packet, pointers, catalog, *, descendants=False):
    if len(pointers) != len(set(pointers)):
        raise ValueError("批量评审依据重复")
    for pointer in pointers:
        if len(pointer) > 240 or not any(
                pointer == ref or (descendants and pointer.startswith(ref + "/"))
                for ref in catalog):
            raise ValueError("批量评审依据必须引用目录中的具体包内记录或允许的字段")
        resolve_packet_pointer(packet, pointer)


def _referenced_owner(packet, pointer):
    """Resolve identity only; a reference does not prove the claimed capability."""
    tokens = _pointer_tokens(pointer)
    for path, field in ((("candidate", "milestones"), "id"),
                        (("candidate", "source_milestones"), "id"),
                        (("candidate", "behaviors"), "owner")):
        if tokens[:len(path)] == path and len(tokens) > len(path):
            return resolve_packet_pointer(packet, "/" + "/".join(tokens[:len(path) + 1]))[field]
    if tokens[:3] == ("candidate", "plan_contract", "bindings") and len(tokens) >= 4:
        binding = resolve_packet_pointer(packet, "/" + "/".join(tokens[:4]))
        for behavior in resolve_packet_pointer(packet, "/candidate/behaviors"):
            if (behavior["id"] == binding["behavior_revision_id"]
                    and behavior["behavior_key"] == binding["behavior_key"]):
                return behavior["owner"]
    return None


def _validate_materiality(candidate, packet, issue, catalog):
    evidence = issue.materiality
    _checked_refs(packet, [evidence.obligation_ref], catalog, descendants=True)
    _checked_refs(packet, evidence.boundary_refs, catalog, descendants=True)
    # An obligation must cite literal source/contract text, not an ID, status,
    # whole candidate copy or reviewer-authored summary. Entailment stays human/model work.
    if not re.fullmatch(
            r"/current_input|/candidate/(?:target/statement|target_draft|"
            r"behaviors/[0-9]+/statement|milestones/[0-9]+/(?:intent|scope/[0-9]+)|"
            r"source_milestones/[0-9]+/source_behaviors/[0-9]+/statement|"
            r"plan_contract/(?:sources/[0-9]+/(?:text|reference_context/(?:references|attachments)/"
            r".+/(?:text|excerpt|content)|evidence/[0-9]+/result/(?:text|excerpt|content))|"
            r"requirements/[0-9]+/quote|process_constraints/[0-9]+/quote))|"
            r"/reference_context/(?:references|attachments)/.+/(?:text|excerpt|content)",
            evidence.obligation_ref):
        raise ValueError("实质问题必须引用准确的来源或契约义务文本字段")
    obligation = resolve_packet_pointer(packet, evidence.obligation_ref)
    if not isinstance(obligation, str) or evidence.obligation_excerpt not in obligation:
        raise ValueError("实质问题的义务摘录必须逐字存在于引用文本")
    requirement = re.fullmatch(r"/candidate/plan_contract/requirements/([0-9]+)/quote",
                               evidence.obligation_ref)
    if requirement:
        row = candidate.plan_contract.requirements[int(requirement[1])]
        if not row.active and "retirement:" + row.id not in issue.subjects:
            raise ValueError("已撤销义务只能用于对应撤销检查")
    owners = evidence.affected_owner_ids
    owner_ids = {m.id for m in [*candidate.source_milestones, *candidate.milestones]}
    if len(owners) != len(set(owners)) or set(owners) - owner_ids:
        raise ValueError("实质问题的受影响归属必须是唯一的候选交付或源码能力 ID")
    expected = {subject.split(":", 1)[1] for subject in issue.subjects
                if subject.startswith("slice_activation:")}
    obligation_owner = _referenced_owner(packet, evidence.obligation_ref)
    if obligation_owner:
        expected.add(obligation_owner)
    if not expected <= set(owners):
        raise ValueError("实质问题的受影响归属未覆盖所引义务或交付检查")
    if evidence.gap_kind == "explicit_conflict" and issue.verdict != "contradicted":
        raise ValueError("明确冲突必须使用 contradicted")
    if evidence.gap_kind == "unresolved_semantics" and issue.verdict != "unknown":
        raise ValueError("未决语义必须使用 unknown")
    if evidence.gap_kind == "unavailable_prerequisite":
        consumer, provider_ref = evidence.consumer_owner_id, evidence.provider_ref
        if consumer not in owners or not provider_ref or provider_ref not in evidence.boundary_refs:
            raise ValueError("不可用前置必须指明受影响消费者及边界中的既有提供者引用")
        provider = _referenced_owner(packet, provider_ref)
        if provider is None:
            raise ValueError("未命名既有提供者应作为 unresolved_semantics，不能虚构归属")
        graph = {m.id: list(m.dependencies)
                 for m in [*candidate.source_milestones, *candidate.milestones]}
        if provider in {consumer, *ancestor_sets(graph)[consumer]}:
            raise ValueError("自身或声明祖先已可用，不能声明为不可用前置")
    elif evidence.consumer_owner_id is not None or evidence.provider_ref is not None:
        raise ValueError("只有不可用前置问题可声明消费者和提供者引用")


def normalize_batch_review(candidate, packet, batch):
    batch = BatchSemanticReview.model_validate(batch.model_dump())
    if batch.candidate_hash != candidate_hash(candidate) or batch.review_scope_hash != packet["review_scope_hash"]:
        raise ValueError("批量评审引用了过期候选或评审范围")
    statuses = {}
    for verdict, subjects in batch.statuses.model_dump().items():
        for subject in subjects:
            if subject in statuses:
                raise ValueError("批量评审的检查身份或状态重复")
            statuses[subject] = verdict
    required = packet["required_subjects"]
    if len(required) != len(set(required)) or set(statuses) != set(required) or required != review_subjects(candidate):
        raise ValueError("批量评审未精确覆盖所有要求的检查项")
    issues, ids = {}, set()
    catalog = set(_expanded_evidence_catalog(packet))
    for issue in batch.issues:
        if issue.id in ids:
            raise ValueError("批量评审的问题身份或依据重复")
        ids.add(issue.id)
        _checked_refs(packet, issue.evidence_refs, catalog)
        _validate_materiality(candidate, packet, issue, catalog)
        for subject in issue.subjects:
            if subject in issues or statuses.get(subject) != issue.verdict:
                raise ValueError("批量评审问题覆盖重复或与逐项状态不一致")
            issues[subject] = issue
    if set(issues) != {subject for subject, verdict in statuses.items() if verdict != "supported"}:
        raise ValueError("批量评审的问题必须精确覆盖每个未通过项")
    eligible_ids = {entry["id"] for entry in eligible_execution_evidence(candidate)}
    for observation in batch.observations:
        if observation.id in ids or len(observation.subjects) != len(set(observation.subjects)):
            raise ValueError("批量评审观察身份或检查项重复")
        ids.add(observation.id)
        if set(observation.subjects) - statuses.keys():
            raise ValueError("批量评审观察引用了未知检查项")
        _checked_refs(packet, observation.evidence_refs, catalog, descendants=True)
        # Reuse the honest execution-basis validator without adding observations
        # to the normalized verdict/coverage channel.
        SemanticCheck(subject=observation.subjects[0], verdict="supported",
                      reason=observation.reason, counterexample=observation.reason,
                      basis=observation.basis, execution_evidence_ids=observation.execution_evidence_ids)
        if set(observation.execution_evidence_ids) - eligible_ids:
            raise ValueError("观察的执行依据不在当前有效验收记录中")
    checks = []
    for subject in required:
        issue = issues.get(subject)
        if issue is None:
            checks.append({
                "subject": subject, "verdict": "supported", "basis": "model_inference",
                "reason": "批量模型未报告此项问题（服务端格式归一说明，非逐项论证）",
                "counterexample": "批量协议未提供此项的单独反例；不代表已执行验收",
                "execution_evidence_ids": [],
            })
        else:
            checks.append({
                "subject": subject, "verdict": issue.verdict,
                "reason": f"[{issue.id}] {issue.reason}", "counterexample": issue.counterexample,
                "basis": issue.basis, "execution_evidence_ids": issue.execution_evidence_ids,
            })
    review = SemanticReview(candidate_hash=batch.candidate_hash, summary=batch.summary, checks=checks)
    validate_semantic_review(candidate, review)
    return review


def batch_review_certificate(raw, batch):
    return {"schema_version": BATCH_VERSION, "raw_arguments": raw, "batch": batch.model_dump(),
            "normalization": "Supported-row reason/counterexample are server-authored disclosure placeholders. "
                             "Materiality evidence and advisory observations remain in the exact batch audit; "
                             "the compatible SemanticReview projection contains verdict checks only."}


def validate_batch_certificate(before, candidate, record):
    """Replay the current certificate at the commit boundary, not merely beside it."""
    if record.get("checker_version") != CHECKER_VERSION:
        raise ValueError("候选评审版本已更新，需要重新评审")
    packet = batch_review_packet(before, candidate, record)
    if not record.get("review_inputs") or record["review_inputs"][-1] != packet:
        raise ValueError("评审依据与当前候选或交付范围不一致")
    certificates = record.get("batch_reviews", [])
    if not certificates or certificates[-1].get("schema_version") != BATCH_VERSION:
        raise ValueError("候选缺少当前批量评审证书")
    certificate = certificates[-1]
    batch = BatchSemanticReview.model_validate_json(certificate["raw_arguments"])
    if batch.model_dump() != certificate.get("batch") or certificate["batch"] != record["report"].get("semantic_batch"):
        raise ValueError("批量评审原始结果与保存结果不一致")
    review = normalize_batch_review(candidate, packet, batch)
    if review.model_dump() != record["report"].get("semantic") or not record.get("reviews") or review.model_dump() != record["reviews"][-1]:
        raise ValueError("批量评审与兼容评审记录不一致")
    return review
