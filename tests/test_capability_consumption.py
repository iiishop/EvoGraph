"""Small offline pairs: declared use is evidence, never mechanism completeness."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from evograph.agent_tools.base import ToolContext
from evograph.application.plan_harness import BUILTIN_REGISTRY, run_deterministic, seal_snapshot
from evograph.application.plan_ir import compile_plan_delta
from evograph.application.plan_review import batch_review_packet, resolve_packet_pointer
from evograph.application.plan_stage import StagedDatabase
from evograph.application.plan_units import _schedule
from evograph.domain.models import BehaviorRevision, Milestone, Project, TargetVersion
from evograph.domain.plan_contracts import (
    ContractBinding,
    IntentSource,
    InvokeCapabilityStep,
    ProvidedCapability,
    Requirement,
    active_behaviors,
    candidate_hash,
)
from evograph.domain.typed_capabilities import (
    capability_changes,
    capability_providers,
    typed_capability_report,
    validate_capability_moves,
)
from pydantic import ValidationError
from test_plan_ir import complete_delta, submit


def capability(key, kind="query", **extra):
    return ProvidedCapability(key=key, kind=kind, action=key, **extra)


def fixture():
    p = Project(name="Declared booking boundary")
    p.plan_contract.sources = [IntentSource(id="source", text="Booking outcome")]
    p.plan_contract.requirements = [Requirement(id="r", source_id="source", quote="Booking outcome")]
    for key, owner, dependencies, caps in (
        ("base", "M1", [], [capability("eligible", consumes=[]),
                            capability("create", "command", consumes=["eligible"]),
                            capability("read", consumes=["eligible"])]),
        ("later", "M2", ["M1"], [capability("timeline", consumes=["eligible"])]),
    ):
        behavior = BehaviorRevision(id=key + "-v1", behavior_key=key, version=1,
                                    statement="eligible create read timeline", owner=owner)
        p.behaviors.append(behavior)
        p.milestones.append(Milestone(id=owner, title=owner, intent=owner, scope=["booking.py"],
                                     dependencies=dependencies,
                                     dependency_reasons={d: "Uses shared rule" for d in dependencies},
                                     behavior_revision_ids=[behavior.id]))
        p.plan_contract.bindings.append(ContractBinding(
            behavior_key=key, behavior_revision_id=behavior.id, requirement_ids=["r"],
            mechanism="Shared booking rule", provides=caps,
            steps=[InvokeCapabilityStep(kind="invoke_" + c.kind, capability_key=c.key, quote=c.action)
                   for c in caps if c.key != "eligible"],
        ))
    p.targets = [TargetVersion(number=1, statement="Booking outcome",
                               required_behavior_ids=[b.id for b in p.behaviors])]
    return p


def checked(p, before=None, **record):
    snapshot = seal_snapshot(before or p, p, {"id": "offline", "base_revision": p.revision,
                                             "input": "Review declarations", **record})
    # Use the registered implementation; full harness execution is checked separately.
    return next(plugin for plugin in BUILTIN_REGISTRY
                if plugin.manifest.id == "typed_capability_flow").check(snapshot)


def codes(report):
    return {finding.code for finding in report.findings}


def test_own_planned_and_ancestor_providers_command_fixture_then_query_are_valid():
    p = fixture()
    assert all(m.status == "PLANNED" for m in p.milestones)
    report = typed_capability_report(p)
    assert checked(p).verdict == report["verdict"] == "pass"
    assert [s.kind for s in p.plan_contract.bindings[0].steps] == ["invoke_command", "invoke_query"]
    assert {e["edge_kind"] for e in report["derived_edges"]} == {
        "acceptance_invocation", "mechanism_consumption"}
    for edge in report["derived_edges"]:
        assert edge["provider_behavior_revision_id"] in {"base-v1", "later-v1"}
        assert edge["consumer_ref"] and edge["provider_ref"]
    run = run_deterministic(seal_snapshot(p, p, {"id": "offline", "base_revision": 0}))
    typed = next(e for e in run.executions if e.plugin_id == "typed_capability_flow")
    assert typed.status == "completed" and typed.result.verdict == "pass"
    assert typed.result.plugin_version == "3"


@pytest.mark.parametrize("case,expected", [
    ("direct", "capability_effect_mismatch"), ("chain", "capability_effect_mismatch"),
    ("missing", "missing_capability_provider"), ("duplicate", "ambiguous_consumed_capability"),
    ("future", "capability_not_available"), ("stale", "stale_capability_binding"),
])
def test_invalid_declared_edges(case, expected):
    p = fixture()
    first, later = p.plan_contract.bindings
    if case == "direct":
        first.provides[2].consumes = ["create"]
    elif case == "chain":
        first.provides[0].consumes = ["create"]  # read -> eligible -> create
    elif case == "missing":
        first.provides.pop(0)
    elif case == "duplicate":
        later.provides.append(capability("eligible", consumes=[]))
    elif case == "future":
        later.provides.append(first.provides.pop(0))
    else:
        first.behavior_revision_id = "inactive-revision"
        assert "eligible" not in capability_providers(p)
    result = checked(p)
    assert result.verdict == "block" and expected in codes(result)
    if case == "duplicate":
        finding = next(f for f in result.findings if f.code == expected)
        assert len(finding.evidence_refs) == 3  # consumer plus both providers


def test_schema_prerequisite_without_runtime_write_and_query_cycles_are_valid():
    p = fixture()
    p.plan_contract.bindings[1].requires_behavior_keys = ["base"]
    p.plan_contract.bindings[1].provides[0].consumes = []
    assert checked(p).verdict == "pass"
    p.plan_contract.bindings[0].provides[0].consumes = ["read"]
    assert checked(p).verdict == "pass"  # No generic operation-cycle ban.
    p.plan_contract.bindings[1].provides[0].consumes = ["create"]
    assert "capability_effect_mismatch" in codes(checked(p))


def test_omission_empty_and_deletion_have_distinct_coverage_and_obligations():
    before = fixture()
    omitted = before.model_copy(deep=True)
    omitted.plan_contract.bindings[0].provides[1].consumes = None
    assert checked(omitted, before).verdict == "unknown"
    assert "capability_consumption_unknown" in codes(checked(omitted, before))
    legacy = typed_capability_report(omitted)
    assert legacy["verdict"] == "pass"
    assert "capability_consumption:create" in legacy["uncovered_subjects"]
    empty = before.model_copy(deep=True)
    empty.plan_contract.bindings[0].provides[1].consumes = []
    assert checked(empty, before).verdict == "pass"
    assert candidate_hash(empty) != candidate_hash(omitted)
    removed = before.model_copy(deep=True)
    removed.plan_contract.bindings[0].provides.pop(1)
    removed.plan_contract.bindings[0].steps.pop(0)
    assert checked(removed, before).verdict == "pass"
    assert typed_capability_report(removed, before)["removed_consumption_declarations"] == [
        {"behavior_key": "base", "capability_key": "create"}]
    retired_packet = batch_review_packet(before, removed, {
        "id": "r", "base_revision": 0, "input": "review retirement"})
    assert retired_packet["capability_coverage"]["removed_consumption_declarations"] == [
        {"behavior_key": "base", "capability_key": "create"}]
    untyped = fixture()
    for b in untyped.plan_contract.bindings:
        b.provides, b.steps = [], None
    assert typed_capability_report(untyped)["verdict"] == "not_applicable"
    assert checked(untyped, before).verdict == "unknown"
    packet = batch_review_packet(untyped, untyped, {"id": "r", "base_revision": 0, "input": "review"})
    assert len(packet["capability_coverage"]["unmodeled_acceptance"]) == 2
    packet = batch_review_packet(omitted, empty, {"id": "r", "base_revision": 0, "input": "review"})
    assert packet["capability_coverage"]["declared_empty_consumption_refs"]
    assert "not prove" in packet["capability_coverage"]["scope"]


@pytest.mark.parametrize("consumes", [[" "], [1], ["x", "x"], "x", ["x"] * 17])
def test_consumption_metadata_is_strict_and_bounded(consumes):
    with pytest.raises(ValidationError):
        capability("read", consumes=consumes)


def test_provider_revision_and_consumer_only_move_or_removal_are_in_exact_review_context():
    before = fixture()
    after = before.model_copy(deep=True)
    revised = after.behaviors[0].model_copy(update={"id": "base-v2", "version": 2, "supersedes": "base-v1"})
    after.behaviors.append(revised)
    after.milestones[0].behavior_revision_ids = [revised.id]
    after.plan_contract.bindings[0].behavior_revision_id = revised.id
    change = next(c for c in capability_changes(before, after) if c["key"] == "eligible")
    assert change["before_providers"][0]["behavior_revision_id"] == "base-v1"
    assert change["after_providers"][0]["behavior_revision_id"] == "base-v2"
    assert {c["behavior_key"] for c in change["after_consumers"]} == {"base", "later"}
    packet = batch_review_packet(before, after, {"id": "r", "base_revision": 0, "input": "review"})
    delta = next(c for c in packet["capability_delta"]["changes"] if c["key"] == "eligible")
    for consumer in delta["after_consumers"]:
        for ref in consumer["consumption_refs"]:
            assert resolve_packet_pointer(packet, ref) == "eligible"
    # Consumer revision/move and removal must be exposed even when provider is unchanged.
    moved = before.model_copy(deep=True)
    moved.behaviors[1].owner = "M1"
    moved.milestones[0].behavior_revision_ids.append("later-v1")
    moved.milestones.pop()
    assert checked(moved).verdict == "pass"
    changed = next(c for c in capability_changes(before, moved) if c["key"] == "eligible")
    assert changed["after_consumers"][1]["owner"] == "M1"
    moved.plan_contract.bindings.pop(1)
    moved.milestones[0].behavior_revision_ids.remove("later-v1")
    changed = next(c for c in capability_changes(before, moved) if c["key"] == "eligible")
    assert len(changed["after_consumers"]) == 1
    moved = before.model_copy(deep=True)
    moved.plan_contract.bindings[1].provides.append(moved.plan_contract.bindings[0].provides.pop(0))
    with pytest.raises(ValueError, match="capability_move_reason"):
        validate_capability_moves(before, moved, {})


class MemoryStore:
    def save(self, record):
        return json.loads(json.dumps(record))


def memory_stage():
    original = Project(name="Offline stage", unified_planning=True)
    candidate = original.model_copy(deep=True)
    from test_plan_patch import INPUT
    candidate.plan_contract.sources.append(IntentSource(id="source", text=INPUT))
    record = {"id": "source", "project": candidate.model_dump(), "base_revision": 0,
              "revision": 0, "input": INPUT}
    db = StagedDatabase(SimpleNamespace(get=lambda _: original.model_copy(deep=True)),
                        MemoryStore(), record, "source")
    return ToolContext(candidate.id, SimpleNamespace(db=db)), db


def test_field_patch_noop_clearing_old_revision_and_checkpoint_erasure():
    ctx, db = memory_stage()
    delta = complete_delta()
    for item in delta["contracts"]:
        key = item["key"]
        item["provides"] = [{"key": key, "kind": "query", "action": key,
                             "consumes": ["core"] if key == "cli" else []}]
        item["steps"] = [{"kind": "invoke_query", "capability_key": key, "quote": key}]
    submit(ctx, delta)
    before = db.project.model_copy(deep=True)
    assert submit(ctx, {"contracts": [{"key": "cli"}]})["status"] == "NO_PROGRESS"
    assert db.project == before
    submit(ctx, {"contracts": [{"key": "cli", "mechanism": "Refine implementation without changing use"}]})
    old = active_behaviors(before)["cli"]
    assert db.project.behaviors[:len(before.behaviors)] == before.behaviors
    assert active_behaviors(db.project)["cli"].supersedes == old.id
    binding = next(b for b in db.project.plan_contract.bindings if b.behavior_key == "cli")
    assert binding.provides[0].consumes == ["core"]
    # Exact explicit [] clears the modeled edge and creates a binding revision.
    provided = binding.provides[0].model_dump()
    provided["consumes"] = []
    submit(ctx, {"contracts": [{"key": "cli", "provides": [provided]}]})
    assert next(b for b in db.project.plan_contract.bindings if b.behavior_key == "cli").provides[0].consumes == []
    provided.pop("consumes")
    submit(ctx, {"contracts": [{"key": "cli", "provides": [provided]}]})
    # No publication here: preserve unknown across later checkpoint/reload, not just adjacent diffs.
    submit(ctx, {"contracts": [{"key": "cli", "mechanism": "Second checkpoint"}]})
    reloaded = StagedDatabase(db.canonical, db.store, deepcopy(db.record), "source")
    assert checked(reloaded.project, consumption_obligation_keys=
                   reloaded.record["consumption_obligation_keys"]).verdict == "unknown"
    assert ("cli", "cli") in map(tuple, reloaded.record["consumption_obligation_keys"])
    # Removing a consumed provider cannot pass final compile/check; use a private pure compile first.
    payload = {"contracts": [{"key": "core", "provides": []}]}
    compile_plan_delta(db.project, payload)
    with pytest.raises(ValueError, match="missing_capability_provider"):
        submit(ctx, payload)


def test_removal_scheduler_requires_consuming_provides_repair():
    p = fixture()
    rows = [{"kind": "remove_contract", "id": "base", "change_id": "remove_contract:base", "fields": [], "uses": []},
            {"kind": "remove_slice", "id": "M1", "change_id": "remove_slice:M1", "fields": [], "uses": []},
            {"kind": "slice", "id": "M2", "change_id": "slice:M2", "fields": ["dependencies"], "uses": []}]
    units = _schedule(p, rows)
    assert any("contract:later.provides" in hold for u in units for hold in u["holds"])
    rows.append({"kind": "contract", "id": "later", "change_id": "contract:later", "fields": ["provides"], "uses": []})
    units = _schedule(p, rows)
    assert not any("contract:later.provides" in hold for u in units for hold in u["holds"])


def test_atomic_capability_retirement_repairs_every_runtime_and_acceptance_reference():
    ctx, db = memory_stage()
    delta = complete_delta()
    for item in delta["contracts"]:
        key = item["key"]
        item["provides"] = [{"key": key, "kind": "query", "action": key,
                             "consumes": ["core"] if key == "cli" else []}]
        item["steps"] = [{"kind": "invoke_query", "capability_key": key, "quote": key}]
    submit(ctx, delta)
    before = db.project.model_copy(deep=True)
    replacement = {"key": "core", "provides": [{"key": "core_new", "kind": "query",
                     "action": "core", "consumes": []}],
                   "steps": [{"kind": "invoke_query", "capability_key": "core_new", "quote": "core"}]}
    # Repaired acceptance alone leaves a dangling runtime consumer.
    with pytest.raises(ValueError, match="missing_capability_provider"):
        submit(ctx, {"contracts": [replacement]})
    consumer = {"key": "cli", "provides": [{"key": "cli", "kind": "query", "action": "cli", "consumes": []}]}
    # Repaired runtime use alone leaves a dangling acceptance invocation.
    with pytest.raises(ValueError, match="missing_capability_provider"):
        submit(ctx, {"contracts": [{k: v for k, v in replacement.items() if k != "steps"}, consumer]})
    submit(ctx, {"contracts": [replacement, consumer]})
    result = checked(db.project, before, consumption_obligation_keys=db.record["consumption_obligation_keys"])
    assert result.verdict == "pass"
    audit = db.record["compilations"][-1]
    removed = next(c for c in audit["capability_changes"] if c["key"] == "core")
    assert removed["before_providers"] and not removed["after_providers"]
    assert removed["before_consumers"] and not removed["after_consumers"]
    assert set(active_behaviors(db.project)) == set(active_behaviors(before))
    assert db.project.behaviors[:len(before.behaviors)] == before.behaviors
    # Temporary retirement must not erase the established obligation on reintroduction.
    submit(ctx, {"contracts": [{"key": "core", "provides": [
        {"key": "core", "kind": "query", "action": "core"}], "steps": [
        {"kind": "invoke_query", "capability_key": "core", "quote": "core"}]}]})
    restored = StagedDatabase(db.canonical, db.store, deepcopy(db.record), "source")
    assert "capability_consumption_unknown" in codes(checked(
        restored.project, consumption_obligation_keys=restored.record["consumption_obligation_keys"]))
    submit(ctx, {"contracts": [{"key": "core", "provides": [], "steps": []}]})
    assert checked(db.project, consumption_obligation_keys=db.record["consumption_obligation_keys"],
                   typed_obligation_keys=db.record["typed_obligation_keys"]).verdict == "unknown"
