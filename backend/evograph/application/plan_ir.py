"""Strict, shallow model protocol compiled into the existing atomic PlanPatch.

This module never writes while compiling. Rich presentation/history stays in
Project; omitted fields retain their exact stored values. The handler passes its
structured input and compiled-structure hash to PlanPatch's checkpoint audit.
Process constraints have their own source-anchored field and are never product
requirements or behavior acceptance contracts.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..agent_tools.base import ToolSpec
from ..domain.models import ArchitectureSpec, Project
from ..domain.plan_contracts import AcceptanceStep, ProvidedCapability, candidate_hash
from ..domain.policies import CHANGE_POLICIES
from .design import validate_diagram
from .plan_contracts import AddProcessConstraint, AddRequirement, RetireRequirement
from .plan_patch import PlanPatch, compiled_plan_hash, propose_plan_patch

PROTOCOL_VERSION = "plan-delta/v3"


class IRModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("omit unchanged fields instead of null")
        return value


class RequirementDelta(AddRequirement, IRModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProcessConstraintDelta(AddProcessConstraint, IRModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RequirementRetirement(RetireRequirement, IRModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ContractDelta(IRModel):
    key: str = Field(min_length=1, max_length=100)
    owner: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,40}$",
                             json_schema_extra={"required_if_new": True, "references": [{"kind": "slice"}]})
    statement: str | None = Field(default=None, min_length=1, max_length=1000,
                                 json_schema_extra={"required_if_new": True})
    requirement_ids: list[str] | None = Field(default=None, min_length=1, max_length=100,
        json_schema_extra={"required_if_new": True, "references": [{"kind": "requirement"}]})
    mechanism: str | None = Field(default=None, min_length=1, max_length=2500,
                                 json_schema_extra={"required_if_new": True})
    component_ids: list[str] = Field(default_factory=list, max_length=40,
                                    json_schema_extra={"references": [{"kind": "component"}]})
    requires_behavior_keys: list[str] = Field(default_factory=list, max_length=100,
                                            json_schema_extra={"references": [{"kind": "contract"}]})
    provides: list[ProvidedCapability] = Field(default_factory=list, max_length=16,
        json_schema_extra={"references": [{"kind": "capability", "path": "key"}]})
    steps: list[AcceptanceStep] = Field(default_factory=list, max_length=24,
        json_schema_extra={"references": [{"kind": "capability", "path": "capability_key"},
                                          {"kind": "requirement", "path": "requirement_id"}]})
    capability_move_reason: str | None = Field(default=None, min_length=1, max_length=240)
    acceptance_scope: Literal["target", "milestone"] = Field(
        default="target",
        description="Final-goal membership only, never delivery timing. Both scopes must hold at "
        "the owning slice using its actual prerequisites; target does not defer acceptance.",
    )
    owner_change_reason: str | None = Field(default=None, min_length=1, max_length=1500)

    @field_validator("key", "statement", "mechanism", "owner_change_reason", "capability_move_reason")
    @classmethod
    def reject_blank(cls, value):
        if not value.strip():
            raise ValueError("contract fields must not be blank")
        return value


class SliceDelta(IRModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    title: str | None = Field(default=None, min_length=1, max_length=120,
                             json_schema_extra={"required_if_new": True})
    intent: str | None = Field(default=None, min_length=1, max_length=1500,
                              json_schema_extra={"required_if_new": True})
    scope: list[str] | None = Field(default=None, min_length=1, max_length=30,
                                   json_schema_extra={"required_if_new": True})
    dependencies: list[str] | None = Field(default=None, max_length=24,
        json_schema_extra={"references": [{"kind": "slice"}]})
    dependency_reasons: dict[str, str] | None = None
    change_types: list[str] = Field(default_factory=list, max_length=12,
        description="Optional existing change metadata. A declared non-product supporting delivery may use only "
                    "documentation/test and milestone-scoped acceptance. Data or operational migration uses data. "
                    "This declaration never permits relabeling product guarantees or inventing components.",
        json_schema_extra={"items": {"type": "string", "enum": sorted(CHANGE_POLICIES)}})

    @field_validator("change_types")
    @classmethod
    def known_change_types(cls, value):
        if value is not None and set(value) - CHANGE_POLICIES.keys():
            raise ValueError("Unknown change_types; use " + ", ".join(sorted(CHANGE_POLICIES)))
        return value


class ComponentDelta(IRModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    label: str | None = Field(default=None, min_length=1, max_length=120,
                              json_schema_extra={"required_if_new": True})
    description: str | None = Field(default=None, min_length=1, max_length=1500,
                                    json_schema_extra={"required_if_new": True})
    role: Literal["frontend", "backend", "database", "security", "cloud", "message", "external"] = "backend"
    source_refs: list[str] = Field(default_factory=list, max_length=40)


class RelationDelta(IRModel):
    source: str = Field(min_length=1, max_length=40,
                        json_schema_extra={"references": [{"kind": "component"}]})
    target: str = Field(min_length=1, max_length=40,
                        json_schema_extra={"references": [{"kind": "component"}]})
    label: str = Field(min_length=1, max_length=120)


class TechnologyDelta(IRModel):
    area: str = Field(min_length=1, max_length=100)
    choice: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=1500)


class ComponentRetirement(IRModel):
    component_id: str = Field(min_length=1, max_length=40)
    milestone_id: str = Field(min_length=1, max_length=40,
                              json_schema_extra={"references": [{"kind": "slice"}]})
    instruction: str = Field(min_length=1, max_length=1500)


class ArchitectureGroupDelta(IRModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    kind: Literal["client", "server", "data", "external", "shared", "domain"] = "shared"
    member_node_ids: list[str] = Field(min_length=1, max_length=40)


class QualityScenarioDelta(IRModel):
    concern: str = Field(min_length=1, max_length=120)
    scenario: str = Field(min_length=1, max_length=1500)
    measure: str = Field(min_length=1, max_length=500)
    approach: str = Field(min_length=1, max_length=1500)


class PlanDelta(IRModel):
    summary: str = Field(default="更新统一规划候选", min_length=1, max_length=2000)
    requirements: list[RequirementDelta] = Field(default_factory=list, max_length=40)
    process_constraints: list[ProcessConstraintDelta] = Field(default_factory=list, max_length=20)
    retire_requirements: list[RequirementRetirement] = Field(default_factory=list, max_length=40)
    contracts: list[ContractDelta] = Field(default_factory=list, max_length=256)
    slices: list[SliceDelta] = Field(default_factory=list, max_length=24)
    components: list[ComponentDelta] = Field(default_factory=list, max_length=40)
    relations: list[RelationDelta] = Field(default_factory=list, max_length=100)
    remove_relations: list[RelationDelta] = Field(default_factory=list, max_length=100)
    remove_slice_ids: list[str] = Field(default_factory=list, max_length=24)
    remove_contract_keys: list[str] = Field(default_factory=list, max_length=256)
    remove_component_ids: list[str] = Field(default_factory=list, max_length=40)
    restore_contract_keys: list[str] = Field(default_factory=list, max_length=256,
        json_schema_extra={"manifest_extra": {"kind": "contract", "field": "restore"}})
    retirements: list[ComponentRetirement] = Field(default_factory=list, max_length=40)
    target: str | None = Field(default=None, min_length=1, max_length=4000)
    architecture_summary: str | None = Field(default=None, min_length=1, max_length=4000,
        json_schema_extra={"manifest_kind": "architecture", "required_if_new": True})
    technologies: list[TechnologyDelta] | None = Field(default=None, min_length=1, max_length=30,
        json_schema_extra={"manifest_kind": "architecture", "required_if_new": True})
    decisions: list[str] | None = Field(default=None, max_length=30,
        json_schema_extra={"manifest_kind": "architecture"})
    # Optional repair/replacement fields, never mandatory for a new architecture.
    architecture_groups: list[ArchitectureGroupDelta] | None = Field(default=None, max_length=12,
        json_schema_extra={"manifest_kind": "architecture", "references": [{"kind": "component", "path": "member_node_ids"}]})
    architecture_milestone_ids: list[str] | None = Field(default=None, max_length=256,
        json_schema_extra={"manifest_kind": "architecture", "references": [{"kind": "slice"}]})
    risks: list[str] | None = Field(default=None, max_length=20,
        json_schema_extra={"manifest_kind": "architecture"})
    quality_scenarios: list[QualityScenarioDelta] | None = Field(default=None, max_length=20,
        json_schema_extra={"manifest_kind": "architecture"})

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)

        def omit_null(node):
            if isinstance(node, dict):
                # Internal compiler/catalog metadata is not provider schema
                # vocabulary. Keep it on model_fields only.
                for key in ("required_if_new", "references", "manifest_kind", "manifest_extra"):
                    node.pop(key, None)
                if "default" in node and node["default"] is None:
                    del node["default"]
                # Python None represents an omitted field internally. The IR
                # validator rejects explicitly supplied null, so its advertised
                # protocol must do the same while leaving required unchanged.
                for union in ("anyOf", "oneOf"):
                    if union not in node:
                        continue
                    alternatives = [item for item in node[union] if item.get("type") != "null"]
                    if len(alternatives) != len(node[union]):
                        node[union] = alternatives
                        if len(alternatives) == 1:
                            del node[union]
                            node.update(alternatives[0])
                for key, child in node.items():
                    if key not in {"default", "const", "enum", "examples"}:
                        omit_null(child)
            elif isinstance(node, list):
                for child in node:
                    omit_null(child)

        omit_null(schema)
        return schema


def required_plan_fields(model, *, new=False):
    """Canonical presence rules shared by compilation and manifest discovery."""
    return {name for name, field in model.model_fields.items()
            if field.is_required() or (new and (field.json_schema_extra or {}).get("required_if_new"))}


def architecture_delta_fields():
    return {name for name, field in PlanDelta.model_fields.items()
            if (field.json_schema_extra or {}).get("manifest_kind") == "architecture"}


@dataclass(frozen=True)
class CompiledPlanDelta:
    patch: PlanPatch
    audit: dict


def _unique(values, path):
    if len(values) != len(set(values)):
        raise ValueError(f"{path}: duplicate identity")


def _known(values, available, path):
    missing = set(values) - set(available)
    if missing:
        raise ValueError(f"{path}: unknown reference: " + ", ".join(str(value) for value in sorted(missing)))


def _relation_key(relation):
    return relation["source"], relation["target"], relation["label"]


def _architecture(project, args, slice_ids):
    operations = (args.components, args.relations, args.remove_relations,
                  args.remove_component_ids, args.retirements)
    metadata = architecture_delta_fields()
    if not any(operations) and not (metadata & args.model_fields_set):
        if project.architectures:
            _known(project.architectures[-1].diagram.milestone_ids, slice_ids,
                   "architecture.diagram.milestone_ids")
        return None
    previous = project.architectures[-1] if project.architectures else None
    if previous:
        specification = previous.model_dump(exclude={"number", "created_at", "retirements"})
    else:
        missing = (required_plan_fields(PlanDelta, new=True) & metadata) - args.model_fields_set
        if missing:
            raise ValueError("new architecture: required fields: " + ", ".join(sorted(missing)))
        specification = {
            "summary": args.architecture_summary,
            "technologies": [item.model_dump() for item in args.technologies],
            "diagram": {"id": "architecture", "title": "系统架构", "nodes": [], "edges": []},
        }
    diagram = specification["diagram"]
    nodes = {item["id"]: item for item in diagram["nodes"]}
    _unique([item.id for item in args.components], "components.id")
    _unique(args.remove_component_ids, "remove_component_ids")
    _known(args.remove_component_ids, nodes, "remove_component_ids")
    if set(args.remove_component_ids) & {item.id for item in args.components}:
        raise ValueError("components: cannot upsert and remove the same ID")
    for cid in args.remove_component_ids:
        del nodes[cid]
    for item in args.components:
        if item.id not in nodes:
            missing = required_plan_fields(ComponentDelta, new=True) - item.model_fields_set
            if missing:
                raise ValueError(f"components.{item.id}: new component requires "
                                 + ", ".join(sorted(missing)))
        # Apply only explicit fields, retaining all omitted presentation/source
        # data and roles on existing nodes. Domain defaults apply only to new IDs.
        nodes[item.id] = {**nodes.get(item.id, {}), **item.model_dump(exclude_unset=True)}
        if "source_refs" in item.model_fields_set:
            _unique(item.source_refs, f"components.{item.id}.source_refs")
            # This is the exact explicitly replaced list, not a model-supplied
            # estimate of references. DesignService validates every real path.
            nodes[item.id]["source_ref_count"] = len(item.source_refs)
    diagram["nodes"] = list(nodes.values())
    additions = [item.model_dump() for item in args.relations]
    removals = [item.model_dump() for item in args.remove_relations]
    added, removed = [_relation_key(item) for item in additions], [_relation_key(item) for item in removals]
    _unique(added, "relations")
    _unique(removed, "remove_relations")
    edges = {_relation_key(item): item for item in diagram.get("edges", [])}
    _known(removed, edges, "remove_relations")
    if set(added) & set(removed):
        raise ValueError("relations: cannot upsert and remove the same relation")
    for key in removed:
        del edges[key]
    edges.update(zip(added, additions))
    diagram["edges"] = list(edges.values())
    for index, edge in enumerate(diagram["edges"]):
        _known([edge["source"], edge["target"]], nodes, f"relations[{index}]")
    if "architecture_groups" in args.model_fields_set:
        diagram["groups"] = [item.model_dump() for item in args.architecture_groups]
    if "architecture_milestone_ids" in args.model_fields_set:
        _unique(args.architecture_milestone_ids, "architecture_milestone_ids")
        diagram["milestone_ids"] = list(args.architecture_milestone_ids)
    _known(diagram.get("milestone_ids", []), slice_ids, "architecture.diagram.milestone_ids")
    # Retained group membership is deliberately not silently pruned by a delete.
    for index, group in enumerate(diagram.get("groups", [])):
        _known(group["member_node_ids"], nodes, f"architecture.diagram.groups[{index}]")
    for field, target in (("architecture_summary", "summary"), ("technologies", "technologies"),
                          ("decisions", "decisions"), ("risks", "risks"),
                          ("quality_scenarios", "quality_scenarios")):
        if field in args.model_fields_set:
            value = getattr(args, field)
            if value is None:
                raise ValueError(f"{field}: omit unchanged fields instead of null")
            specification[target] = [v.model_dump() for v in value] if field in {
                "technologies", "quality_scenarios",
            } else value
    _unique([item.component_id for item in args.retirements], "retirements.component_id")
    _known([item.milestone_id for item in args.retirements], slice_ids, "retirements.milestone_id")
    if set(args.remove_component_ids) != {item.component_id for item in args.retirements}:
        raise ValueError("retirements: every removed component needs exactly one migration owner")
    # Retirements is this transition's delta; prior revisions keep their history.
    specification["retirements"] = [item.model_dump() for item in args.retirements]
    result = ArchitectureSpec.model_validate(specification)
    validate_diagram(result.diagram)
    return result


def compile_plan_delta(project: Project, delta: PlanDelta | dict) -> CompiledPlanDelta:
    """Pure structural compiler; PlanPatch still owns final domain/source checks."""
    raw = delta.model_dump(mode="json", exclude_unset=True) if isinstance(delta, PlanDelta) else deepcopy(delta)
    args = PlanDelta.model_validate(raw, strict=True)
    raw = args.model_dump(mode="json", exclude_unset=True)
    slices = {item.id: item for item in project.milestones}
    _unique([item.id for item in project.milestones], "existing slices.id")
    _unique([item.id for item in args.slices], "slices.id")
    _unique([item.key for item in args.contracts], "contracts.key")
    _unique([item.id for item in args.requirements], "requirements.id")
    _unique([item.id for item in args.process_constraints], "process_constraints.id")
    _unique([item.id for item in args.retire_requirements], "retire_requirements.id")
    for name in ("remove_slice_ids", "remove_contract_keys", "restore_contract_keys"):
        _unique(getattr(args, name), name)
    _known(args.remove_slice_ids, slices, "remove_slice_ids")
    updates = {item.id: item for item in args.slices}
    removed = set(args.remove_slice_ids)
    if removed & updates.keys():
        raise ValueError("slices: cannot upsert and remove the same ID")
    final_slice_ids = (slices.keys() | updates.keys()) - removed
    by_id = {item.id: item for item in project.behaviors}
    bindings = {item.behavior_key: item for item in project.plan_contract.bindings}
    _unique([item.behavior_key for item in project.plan_contract.bindings], "existing bindings.key")
    existing, owners = {}, {}
    for milestone in project.milestones:
        for bid in milestone.behavior_revision_ids:
            _known([bid], by_id, f"existing slices.{milestone.id}.behavior_revision_ids")
            behavior = by_id[bid]
            if behavior.owner != milestone.id or behavior.behavior_key in owners:
                raise ValueError(f"contracts.{behavior.behavior_key}: ambiguous ownership")
            owners[behavior.behavior_key] = milestone.id
            value = {"key": behavior.behavior_key, "owner": milestone.id,
                     "statement": behavior.statement, "acceptance_scope": behavior.acceptance_scope}
            binding = bindings.get(behavior.behavior_key)
            if binding:
                value.update(binding.model_dump(exclude={"behavior_key", "behavior_revision_id"}))
            existing[behavior.behavior_key] = value
    _known(args.remove_contract_keys, existing, "remove_contract_keys")
    supplied = {item.key: item for item in args.contracts}
    if set(args.remove_contract_keys) & supplied.keys():
        raise ValueError("contracts: cannot upsert and remove the same key")
    _known(args.restore_contract_keys, supplied, "restore_contract_keys")
    contracts = {key: deepcopy(value) for key, value in existing.items()
                 if key not in args.remove_contract_keys and value["owner"] not in removed}
    affected = set(updates)
    for key in args.remove_contract_keys:
        affected.add(owners[key])
    for item in args.contracts:
        changes = item.model_dump(exclude_unset=True, exclude={"owner_change_reason"})
        old = existing.get(item.key)
        if (old and old.get("steps") is not None and "statement" in changes
                and changes["statement"] != old["statement"] and "steps" not in changes):
            raise ValueError(f"contracts.{item.key}: changed typed statement requires explicit steps")
        resolved = {**existing.get(item.key, {}), **changes}
        missing = required_plan_fields(ContractDelta, new=True) - resolved.keys()
        if missing:
            raise ValueError(f"contracts.{item.key}: new or incomplete contract requires "
                             + ", ".join(sorted(missing)))
        _known([resolved["owner"]], final_slice_ids, "contracts.owner")
        moved = item.key in owners and owners[item.key] != resolved["owner"]
        if moved and item.owner_change_reason is None:
            raise ValueError(f"contracts.{item.key}: owner change requires owner_change_reason")
        if not moved and item.owner_change_reason is not None:
            raise ValueError(f"contracts.{item.key}: owner_change_reason requires an active owner change")
        contracts[item.key] = resolved
        if {k: v for k, v in resolved.items() if k != "capability_move_reason"} != existing.get(item.key):
            affected.add(resolved["owner"])
            if item.key in owners:
                affected.add(owners[item.key])
    affected -= removed
    if len(affected) > 24:
        raise ValueError("slices: a delta may affect at most 24 owners")
    _known([item["owner"] for item in contracts.values()], final_slice_ids, "contracts.owner")
    architecture = _architecture(project, args, final_slice_ids)
    component_ids = {item.id for item in architecture.diagram.nodes} if architecture else (
        {item.id for item in project.architectures[-1].diagram.nodes} if project.architectures else set()
    )
    requirement_ids = {item.id for item in project.plan_contract.requirements if item.active}
    _known([item.id for item in args.retire_requirements], requirement_ids, "retire_requirements.id")
    if {item.id for item in args.requirements} & {item.id for item in args.retire_requirements}:
        raise ValueError("requirements: cannot add and retire the same ID")
    requirement_ids |= {item.id for item in args.requirements}
    requirement_ids -= {item.id for item in args.retire_requirements}
    for key, contract in contracts.items():
        for field, available in (("requirement_ids", requirement_ids), ("component_ids", component_ids),
                                 ("requires_behavior_keys", contracts)):
            values = contract.get(field, [])
            _unique(values, f"contracts.{key}.{field}")
            _known(values, available, f"contracts.{key}.{field}")
        if not contract.get("requirement_ids") or not contract.get("mechanism"):
            raise ValueError(f"contracts.{key}: existing contract lacks trace data; submit its complete upsert")
    milestones = []
    metadata = {"id", "title", "intent", "scope", "dependencies", "dependency_reasons",
                "attachment_ids", "resources", "change_types"}
    ordered_ids = list(slices) + [mid for mid in updates if mid not in slices]
    for mid in ordered_ids:
        if mid in removed:
            continue
        old = slices.get(mid)
        payload = old.model_dump(include=metadata) if old else {"id": mid}
        change = updates.get(mid)
        if change:
            changes = change.model_dump(exclude_unset=True)
            if any(value is None for value in changes.values()):
                raise ValueError(f"slices.{mid}: omit unchanged fields instead of null")
            payload.update(changes)
            if "dependencies" in changes and "dependency_reasons" not in changes:
                payload["dependency_reasons"] = {
                    key: value for key, value in payload.get("dependency_reasons", {}).items()
                    if key in changes["dependencies"]
                }
        if not old:
            missing = required_plan_fields(SliceDelta, new=True) - payload.keys()
            if missing:
                raise ValueError(f"slices.{mid}: new slice requires " + ", ".join(str(value) for value in sorted(missing)))
        dependencies = payload.get("dependencies", [])
        reasons = payload.get("dependency_reasons", {})
        _unique(dependencies, f"slices.{mid}.dependencies")
        _known(dependencies, final_slice_ids | {m.id for m in project.source_milestones},
               f"slices.{mid}.dependencies")
        _known(reasons, dependencies, f"slices.{mid}.dependency_reasons")
        if any(not reasons.get(dependency, "").strip() for dependency in dependencies):
            raise ValueError(f"slices.{mid}.dependency_reasons: every dependency requires a reason")
        if mid not in affected:
            continue
        payload["behaviors"] = [{k: v for k, v in contract.items() if k != "owner"}
                                for contract in contracts.values() if contract["owner"] == mid]
        if not payload["behaviors"]:
            raise ValueError(f"slices.{mid}: no contracts remain; explicitly remove the empty slice")
        payload["restore_inactive_behavior_keys"] = [
            key for key in args.restore_contract_keys if contracts[key]["owner"] == mid
        ]
        milestones.append(payload)
    patch_data = {
        "summary": args.summary,
        "add_requirements": [item.model_dump(exclude_unset=True) for item in args.requirements],
        "process_constraints": [item.model_dump(exclude_unset=True) for item in args.process_constraints],
        "retire_requirements": [item.model_dump(exclude_unset=True) for item in args.retire_requirements],
        "milestones": milestones, "remove_milestone_ids": args.remove_slice_ids,
    }
    if args.target is not None:
        patch_data["target"] = args.target
    if architecture is not None:
        patch_data["architecture"] = architecture
    patch = PlanPatch.model_validate(patch_data)
    return CompiledPlanDelta(patch, {"protocol_version": PROTOCOL_VERSION, "ir": raw,
                                   "compiled_hash": compiled_plan_hash(patch),
                                   "project_id": project.id, "base_revision": project.revision,
                                   "base_candidate_hash": candidate_hash(project)})


def submit_plan_delta(ctx, args):
    db = ctx.application.db
    if getattr(db, "segmented_planning", False):
        from .plan_units import validate_unit_delta
        if validate_unit_delta(db, args):
            pin = db.record["unit_request"]
            db.record.setdefault("unit_replays", []).append({
                "unit_id": pin["unit_id"], "delta_hash": pin["accepted_delta_hash"],
                "candidate_hash": candidate_hash(db.project), "revision": db.project.revision,
                "effect": "no_progress_exact_replay",
            })
            db.record = db.store.save(db.record)
            ctx.candidate_ready = False
            return {"node_ids": [], "effect": "updated", "status": "NO_PROGRESS",
                    "candidate_state": "staged", "publication_requested": False,
                    "replayed_unit_id": pin["unit_id"], "saved_revision": db.project.revision}
    compiled = compile_plan_delta(db.get(ctx.project_id), args)
    result = propose_plan_patch(ctx, compiled.patch, compiler_audit=compiled.audit)
    if getattr(db, "segmented_planning", False):
        ctx.candidate_ready = False
        result = {**result, "candidate_state": "staged", "publication_requested": False,
                  "saved_revision": db.project.revision,
                  "saved_segments": db.record.get("generation_progress", {}).get("checkpoint_count", 0),
                  "next": "Continue only remaining changes; call validate_candidate when the whole request is represented."}
    return result


DELTA_TOOL = ToolSpec(
    "submit_plan_delta",
    "Save one complete, reference-closed candidate segment atomically; this is not final publication. "
    "Prefer a small coherent group of affected contracts/slices; do not repeat whole unchanged fields. "
    "Use further segments when needed, then validate_candidate explicitly. omissions retain data, null is invalid. requirements quote allowed user "
    "sources; process_constraints govern this turn, not product acceptance. Existing contracts need "
    "only key and changed fields; every omitted field remains exact. New contracts need owner, "
    "statement, requirement_ids and mechanism. provides/steps are an OPTIONAL typed extension; do not "
    "automatically add them or migrate untyped contracts. provides declares stable operation keys with command/query "
    "kind and action quoted exactly from its provider statement. Optional provides[].consumes lists "
    "runtime capability keys for meaningful shared/public operations, not ordinary helpers. Omitted "
    "consumes is unmodeled; [] declares no use within the modeled surface, not mechanism completeness. "
    "A provides list replaces its entries; retain consumes explicitly when replacing a declared entry. "
    "Queries cannot consume commands. Own-slice planned capabilities and ancestors are structurally "
    "available, not already executed. Data/schema prerequisites use requires_behavior_keys, and fixture "
    "commands followed by query acceptance steps are valid. New consumed providers must be available "
    "in the same atomic segment or earlier; manifest provides uses still declare definitions only. "
    "steps invokes those capabilities with "
    "invoke_command/invoke_query and an exact quote from the consumer statement. inspect also needs a "
    "linked active constraint/exclusion requirement_id; outcome contracts require an invocation. "
    "Only still-active contracts with existing or newly supplied nonempty typed declarations require "
    "steps; clearing their declarations cannot opt out and missing/empty steps are unknown. Untyped "
    "contracts remain outside this check. [] clears provides or steps, "
    "component_ids or requires_behavior_keys. A changed typed statement requires explicit steps. "
    "Moving a capability key between provider contracts requires capability_move_reason on the receiver. "
    "Owner moves need owner_change_reason and "
    "retain the stable key/history. Only send actual changes, never rephrase omitted text. "
    "New slices need title/intent/scope (work boundaries). Existing components need only id and changed "
    "fields; omitted fields remain exact. New components need label and description. source_refs replace "
    "real repository paths ([] clears). relations upsert "
    "exact source/target/label triples; remove_relations deletes exact triples. Deletions require "
    "repairing surviving links. Component retirements need unfinished migration owners. "
    "New architecture needs architecture_summary, "
    "technologies and components. Optional architecture_groups, architecture_milestone_ids, risks "
    "and quality_scenarios replace whole lists; [] clears, omission preserves. Historical restoration "
    "needs restore_contract_keys and current user support. Invalid input changes nothing.",
    PlanDelta, submit_plan_delta, "编译统一规划变更", "updated", None,
)
