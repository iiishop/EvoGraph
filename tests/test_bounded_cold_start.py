"""Offline structural/admission evidence; never a joint model-quality result."""
import asyncio
import json
from copy import deepcopy

import pytest
from evograph.application.plan_batch_policy import (
    BATCH_POLICY,
    COLD_START_EXPERIMENT,
    REPAIR_EXPERIMENT,
    empty_planning_base,
    experiment_policy,
    select_experiment,
)
from evograph.application.plan_budget import BudgetedSettings, BudgetExceededError, encoded_size
from evograph.application.plan_ir import PlanDelta, submit_plan_delta
from evograph.application.plan_units import (
    BATCH_UNIT_VERSION,
    MAX_UNIT_BYTES,
    MAX_UNIT_CONTRACTS,
    MAX_UNIT_OPERATIONS,
    _delta_rows,
    all_units_complete,
    manifest_tool_for,
    prepare_unit_request,
    schedule_findings,
    validate_unit_delta,
)
from evograph.application.unified_planning import GENERATOR, generator_prompt
from test_agent_stream import tool_chunks
from test_architecture_unit_bootstrap import manifest_for, schedule, valid_delta
from test_plan_patch import INPUT, initial, stage
from test_unified_planning import enable


def opt_in(db):
    db.segmented_planning = True
    db.record["planning_experiment"] = {"project_id": db.project.id, **experiment_policy(COLD_START_EXPERIMENT)}


def batch(app):
    ctx, db = stage(app)
    opt_in(db)
    raw = valid_delta(db.source_id)
    schedule(ctx, manifest_for(raw))
    prepare_unit_request(db)
    return ctx, db, raw


def nested_consumption_delta(source_id, reference_offset=0):
    """Synthetic reference fanout; not a recommended capability design."""
    raw = valid_delta(source_id)
    first = raw["contracts"][0]
    raw["contracts"] = [deepcopy(first), deepcopy(first)]
    capabilities = []
    for index, contract in enumerate(raw["contracts"]):
        contract["key"] = f"c{index}"
        contract["provides"] = [
            {"key": f"k{index}_{i}", "kind": "query", "action": "Report", "consumes": []}
            for i in range(16)]
        contract["steps"] = [{"kind": "invoke_query", "capability_key": f"k{index}_0", "quote": "Report"}]
        capabilities.extend(contract["provides"])
    actual = _delta_rows(PlanDelta.model_validate(raw))[1]
    base_refs = sum(len(row["uses"]) for row in actual.values())
    remaining = BATCH_POLICY["bounds"]["references"] + reference_offset - base_refs
    assert 0 <= remaining <= len(capabilities) * 16
    for capability in capabilities:
        count = min(16, remaining)
        capability["consumes"] = [f"k0_{i}" for i in range(count)]
        remaining -= count
    assert remaining == 0
    return raw


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_actual_reference_bound_includes_nested_consumption(app, offset):
    ctx, db = stage(app)
    opt_in(db)
    raw = nested_consumption_delta(db.source_id, offset)
    schedule(ctx, manifest_for(raw))
    prepare_unit_request(db)
    before = db.get(db.project.id).model_dump()
    if offset > 0:
        with pytest.raises(ValueError, match="bounded batch actual references exceed policy"):
            submit_plan_delta(ctx, PlanDelta.model_validate(raw))
        assert db.get(db.project.id).model_dump() == before
        assert not all_units_complete(db.record)
    else:
        submit_plan_delta(ctx, PlanDelta.model_validate(raw))
        assert all_units_complete(db.record)


def test_nested_consumption_must_stay_inside_admitted_capabilities(app):
    ctx, db = stage(app)
    opt_in(db)
    raw = nested_consumption_delta(db.source_id, -1)
    raw["contracts"][0]["provides"][0]["consumes"][0] = "outside-cohort"
    schedule(ctx, manifest_for(raw))
    prepare_unit_request(db)
    before = db.get(db.project.id).model_dump()
    with pytest.raises(ValueError, match="actual reference is outside its closed manifest"):
        submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    assert db.get(db.project.id).model_dump() == before
    assert not all_units_complete(db.record)


def test_default_and_saved_schedule_unchanged_when_flag_appears(app):
    ctx, db = stage(app)
    rows = manifest_for(valid_delta(db.source_id))
    saved = deepcopy(schedule(ctx, rows))
    opt_in(db)
    with pytest.raises(ValueError, match="new empty planning base"):
        schedule(ctx, rows)
    assert db.record["work_units"] == saved
    assert saved["version"] == "plan-units/v2" and "batch_admission" not in saved
    assert (MAX_UNIT_OPERATIONS, MAX_UNIT_CONTRACTS, MAX_UNIT_BYTES) == (8, 3, 6144)


def test_one_atomic_batch_exact_replay_and_policy_contract(app):
    ctx, db, raw = batch(app)
    saved = db.record["work_units"]
    assert saved["version"] == BATCH_UNIT_VERSION and len(saved["units"]) == 1
    assert saved["batch_admission"]["policy"] == BATCH_POLICY
    assert db.record["unit_request"]["batch_admission"] == saved["batch_admission"]
    assert "Never combine the whole remaining plan" not in generator_prompt(db.record)
    assert "ONE bounded empty-plan batch" in generator_prompt(db.record)
    assert generator_prompt({}) == GENERATOR
    assert "3-contract/6-KiB" not in manifest_tool_for(db.record).description
    assert not schedule_findings(db.record, db.project)
    result = submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    assert result["candidate_state"] == "staged" and all_units_complete(db.record)
    assert len(db.record["work_units"]["checkpoints"]) == 1
    assert validate_unit_delta(db, raw) is True
    tampered = {**raw, "architecture_summary": "Different replay"}
    with pytest.raises(ValueError, match="already completed"):
        validate_unit_delta(db, tampered)


@pytest.mark.parametrize("mutate", [
    lambda d: d["batch_admission"]["policy"]["bounds"].update(operations=100),
    lambda d: d.pop("batch_admission"),
    lambda d: d.update(version="plan-units/v2"),
    lambda d: d["units"][0].update(estimated_text_bytes=1),
    lambda d: d["units"][0].update(contract_count=0),
    lambda d: d["units"][0].update(depends_on=["unit-001"]),
    lambda d: d["units"][0].update(holds=["unresolved"]),
])
def test_schedule_tampering_rejected_before_pin_and_closure(app, mutate):
    _, db, _ = batch(app)
    mutate(db.record["work_units"])
    assert schedule_findings(db.record, db.project)
    assert prepare_unit_request(db)["current_unit"] is None
    assert not all_units_complete(db.record)


@pytest.mark.parametrize("state", ["existing", "checkpoint", "prior_schedule", "target_draft", "research"])
def test_nonempty_or_checkpoint_base_rejected(app, state):
    ctx, db = stage(app)
    if state == "existing":
        initial(ctx)
    elif state == "checkpoint":
        db.record["generation_progress"] = {"checkpoint_count": 1}
    elif state == "prior_schedule":
        db.record["prior_work_units"] = {"completed_ids": ["old"]}
    elif state == "target_draft":
        db.project.target_draft = "Existing request"
    else:
        db.project.source_analysis_summary = "Existing analysis"
    opt_in(db)
    with pytest.raises(ValueError, match="empty planning base"):
        schedule(ctx, manifest_for(valid_delta(db.source_id)))


@pytest.mark.parametrize("mutation", ["missing_owner", "unknown_reference", "duplicate_reference", "uncovered_slice"])
def test_manifest_closure_failure_never_creates_schedule(app, mutation):
    ctx, db = stage(app)
    opt_in(db)
    rows = manifest_for(valid_delta(db.source_id))
    contract = next(r for r in rows if r["kind"] == "contract")
    if mutation == "missing_owner":
        contract["uses"] = [u for u in contract["uses"] if u["field"] != "owner"]
    elif mutation == "unknown_reference":
        contract["uses"].append({"field": "component_ids", "kind": "component", "id": "unknown"})
    elif mutation == "duplicate_reference":
        contract["uses"].append(deepcopy(contract["uses"][0]))
    else:
        rows.append({"kind": "slice", "id": "unused", "fields": ["title", "intent", "scope"], "uses": [], "intent": "Missing owner"})
    with pytest.raises(ValueError, match="bounded batch"):
        schedule(ctx, rows)
    assert "work_units" not in db.record


def test_count_boundary_and_actual_output_bytes(app):
    ctx, db = stage(app)
    opt_in(db)
    raw = valid_delta(db.source_id)
    while len(manifest_for(raw)) < 32:
        raw["components"].append({"id": f"extra-{len(raw['components'])}", "label": "Extra", "description": "Owned responsibility"})
    rows = manifest_for(raw)
    schedule(ctx, rows)
    assert len(db.record["work_units"]["manifest"]) == 32
    ctx2, db2 = stage(app)
    opt_in(db2)
    extra = {"kind": "component", "id": "too-many", "fields": ["label", "description"], "uses": [], "intent": "Bound"}
    with pytest.raises(ValueError, match="operations 33"):
        schedule(ctx2, [*rows, extra])
    prepare_unit_request(db)
    # UTF-8, not Python character count. Test exact admitted boundary and +1.
    for component in raw["components"]:
        component["description"] = "x"
    remaining = BATCH_POLICY["bounds"]["delta_bytes"] - encoded_size(raw)
    for component in raw["components"]:
        amount = min(remaining, 4497)
        component["description"] += "界" * (amount // 3) + "x" * (amount % 3)
        remaining -= amount
    assert remaining == 0 and encoded_size(raw) == BATCH_POLICY["bounds"]["delta_bytes"]
    validate_unit_delta(db, raw)
    too_big = deepcopy(raw)
    too_big["summary"] = "Beyond boundary"
    with pytest.raises(ValueError, match="delta bytes"):
        validate_unit_delta(db, too_big)


def test_pin_and_completed_compiler_audit_are_revalidated(app):
    ctx, db, raw = batch(app)
    pin = deepcopy(db.record["unit_request"])
    db.record["unit_request"].pop("batch_admission")
    with pytest.raises(ValueError, match="pin changed"):
        validate_unit_delta(db, raw)
    db.record["unit_request"] = pin
    submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    assert all_units_complete(db.record)
    db.record["compilations"][0]["ir"]["summary"] = "Changed audit"
    assert not all_units_complete(db.record)


def test_project_stage_mismatch_and_existing_candidate_rejected(app):
    project = enable(app)
    assert empty_planning_base(project)
    with pytest.raises(ValueError, match="mismatch"):
        select_experiment({"project_id": "wrong", "version": COLD_START_EXPERIMENT}, project, None, False)
    with pytest.raises(ValueError, match="new strictly empty"):
        select_experiment({"project_id": project.id, "version": COLD_START_EXPERIMENT}, project, {}, True)
    with pytest.raises(ValueError, match="actionable"):
        select_experiment({"project_id": project.id, "version": REPAIR_EXPERIMENT}, project, {}, False)


def collect_experiment(app, project, version=COLD_START_EXPERIMENT):
    async def run():
        return [e async for e in app.unified.stream(project.id, INPUT,
            experiment={"project_id": project.id, "version": version})]
    return asyncio.run(run())


@pytest.mark.parametrize("bad_stage", ["router", "generation"])
def test_first_failed_response_has_no_paid_retry(app, bad_stage):
    project = enable(app)
    seen = []
    async def stream(messages, tools):
        names = {t["function"]["name"] for t in tools}
        seen.append(names)
        if "schedule_plan_changes" in names:
            raw = valid_delta("unused-source")
            args = {"changes": [] if bad_stage == "router" else manifest_for(raw)}
            async for event in tool_chunks("schedule_plan_changes", args):
                yield event
        else:
            async for event in tool_chunks("submit_plan_delta", {"target": "Incomplete assigned unit"}):
                yield event
    app.settings.stream = stream
    collect_experiment(app, project)
    record = app.unified.store.latest(project.id)
    assert len(seen) == (1 if bad_stage == "router" else 2)
    assert record["metrics"]["budget"]["max_calls"] == 5
    assert record["metrics"]["planning_experiment"]["limits"]["max_calls"] == 3
    assert record["generation_progress"]["checkpoint_count"] == 0
    assert app.db.get(project.id) == project


def test_dispatch_gate_rejects_retry_or_fourth_call(app):
    project = enable(app)
    selected = {"project_id": project.id, **experiment_policy(COLD_START_EXPERIMENT)}
    metrics = {"provider_calls": 1, "input_bytes": 0, "tokens": 0, "planning_experiment": selected}
    async def forbidden(*args, **kwargs):
        raise AssertionError("Must stop before provider dispatch")
        yield
    app.settings.stream = forbidden
    async def run():
        wrapper = BudgetedSettings(app.settings, metrics)
        tools = [{"function": {"name": "schedule_plan_changes"}}]
        with pytest.raises(BudgetExceededError):
            async for _ in wrapper.stream([], tools):
                pass
    asyncio.run(run())
    assert metrics["admission_rejections"][-1]["reason"] == "experiment_request_sequence"
    assert metrics["provider_calls"] == 1
    metrics["provider_calls"] = 3
    asyncio.run(run())
    assert metrics["provider_calls"] == 3


def test_mock_protocol_three_calls_then_held_and_no_same_stage_repair(app):
    project = enable(app)
    seen = []
    async def stream(messages, tools):
        names = {t["function"]["name"] for t in tools}
        seen.append(names)
        source_id = app.unified.store.latest(project.id)["id"]
        raw = valid_delta(source_id)
        if "schedule_plan_changes" in names:
            name, args = "schedule_plan_changes", {"changes": manifest_for(raw)}
        elif "submit_plan_delta" in names:
            assert "ONE bounded empty-plan batch" in messages[0]["content"]
            name, args = "submit_plan_delta", raw
        else:
            packet = json.loads(messages[-1]["content"])
            subject = "cross_contract_consistency"
            behavior = packet["candidate"]["behaviors"][0]
            name, args = "submit_plan_review", {
                "candidate_hash": packet["candidate_hash"], "review_scope_hash": packet["review_scope_hash"],
                "summary": "Offline unknown fixture; no semantic judgment", "statuses": {
                    "supported": [s for s in packet["required_subjects"] if s != subject],
                    "contradicted": [], "unknown": [subject]},
                "issues": [{"id": "offline-unknown", "subjects": [subject],
                            "materiality": {
                                "obligation_ref": "/candidate/behaviors/0/statement",
                                "obligation_excerpt": behavior["statement"][:160],
                                "affected_owner_ids": [behavior["owner"]],
                                "gap_kind": "unresolved_semantics",
                                "boundary_refs": ["/candidate/plan_contract/bindings/0/mechanism"],
                                "necessary_plan_change": "Offline injected outcome-boundary decision"},
                            "verdict": "unknown", "reason": "Not independently assessed in offline fixture",
                            "counterexample": "No joint model inference occurred",
                            "evidence_refs": ["/candidate/behaviors/0",
                                              "/candidate/plan_contract/bindings/0"]}],
                "observations": []}
            if packet["review_protocol"] == "semantic-batch/v3":
                args["coverage"] = {s: verdict for verdict, subjects in args.pop("statuses").items()
                                    for s in subjects}
        async for event in tool_chunks(name, args):
            yield event
    app.settings.stream = stream
    collect_experiment(app, project)
    record = app.unified.store.latest(project.id)
    assert len(seen) == 3, record.get("report")
    assert record["status"] == "needs_resolution"
    assert record.get("generation_pause_reason") == "experiment_review_complete", record.get("report")
    assert len(record["reviews"]) == 1 and all_units_complete(record)
    assert app.db.get(project.id) == project
    selected = select_experiment({"project_id": project.id, "version": REPAIR_EXPERIMENT}, project, record, True)
    assert selected["version"] == REPAIR_EXPERIMENT


@pytest.mark.parametrize("kind,limit", [("slices", 6), ("contracts", 8)])
def test_delivery_and_contract_count_bounds(app, kind, limit):
    for count in (limit, limit + 1):
        ctx, db = stage(app)
        opt_in(db)
        raw = valid_delta(db.source_id)
        for i in range(1, count):
            contract = deepcopy(raw["contracts"][0])
            contract["key"] = f"contract-{i}"
            if kind == "slices":
                delivery = deepcopy(raw["slices"][0])
                delivery["id"] = f"DELIVERY_{i}"
                contract["owner"] = delivery["id"]
                raw["slices"].append(delivery)
            raw["contracts"].append(contract)
        if count == limit:
            schedule(ctx, manifest_for(raw))
            assert not schedule_findings(db.record, db.project)
        else:
            with pytest.raises(ValueError, match=kind):
                schedule(ctx, manifest_for(raw))


def test_text_only_router_never_counts_as_completed_experiment(app):
    project = enable(app)
    calls = []
    async def stream(messages, tools):
        calls.append(messages)
        yield {"type": "text", "text": "No manifest supplied"}
    app.settings.stream = stream
    collect_experiment(app, project)
    record = app.unified.store.latest(project.id)
    assert len(calls) == 1
    assert record["status"] != "applied" and not record.get("applied_noop")
    assert not all_units_complete(record)
    assert app.db.get(project.id) == project


RESIDUE = [
    ("light_checks", [{"milestone_id": "old", "baseline_id": "old", "fingerprint": "old",
                       "kind": "test", "rationale": "prior check", "result": "PASS", "output": "prior execution"}]),
    ("acceptance_requests", [{"milestone_id": "old", "baseline_id": "old", "fingerprint": "old",
                              "behavior_revision_ids": ["old"], "architecture_revision": 1}]),
    ("uml_diagrams", [{"id": "old-design", "title": "Retained design", "kind": "class",
                       "source": "class Old", "scope": "Prior design"}]),
]


@pytest.mark.parametrize("field,value", RESIDUE)
def test_execution_design_residue_rejected_at_admission_and_replay(app, field, value):
    from evograph.domain.models import Project

    ctx, db = stage(app)
    db.project = Project.model_validate({**db.project.model_dump(), field: value})
    assert not empty_planning_base(db.project)
    with pytest.raises(ValueError, match="new strictly empty"):
        select_experiment({"project_id": db.project.id, "version": COLD_START_EXPERIMENT}, db.project, None, False)
    opt_in(db)
    with pytest.raises(ValueError, match="empty planning base"):
        schedule(ctx, manifest_for(valid_delta(db.source_id)))
    _, fresh, _ = batch(app)
    admission = fresh.record["work_units"]["batch_admission"]
    assert admission["residue"][field] == []
    admission["residue"][field] = value
    assert schedule_findings(fresh.record, fresh.project)
    assert prepare_unit_request(fresh)["current_unit"] is None


@pytest.mark.parametrize("location,field,value,rehash", [
    ("checkpoint", "source_id", "wrong-source", False),
    ("checkpoint", "candidate_hash", "wrong-result", False),
    ("audit", "result_candidate_hash", "wrong-result", False),
    ("audit", "result_candidate_hash", "wrong-result", True),
    ("audit", "project_id", "wrong-project", True),
    ("audit", "base_candidate_hash", "wrong-base", True),
    ("audit", "compiled_hash", "wrong-compiled", True),
    ("audit", "audit_hash", "wrong-audit", False),
    ("audit", "contract_changes", [], True),
])
def test_completed_checkpoint_authoritative_identity_tamper_rejected(app, location, field, value, rehash):
    from evograph.application.plan_units import _hash

    ctx, db, raw = batch(app)
    submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    assert all_units_complete(db.record)
    audit = db.record["compilations"][0]
    target = db.record["work_units"]["checkpoints"][0] if location == "checkpoint" else audit
    target[field] = value
    if rehash:
        audit["audit_hash"] = _hash({k: v for k, v in audit.items() if k != "audit_hash"})
    assert schedule_findings(db.record, db.project)
    assert not all_units_complete(db.record)
    with pytest.raises(ValueError, match="invalid unit schedule"):
        validate_unit_delta(db, raw)


def test_equal_forged_checkpoint_and_audit_hashes_do_not_prove_result(app):
    from evograph.application.plan_units import _hash

    ctx, db, raw = batch(app)
    submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    checkpoint = db.record["work_units"]["checkpoints"][0]
    audit = db.record["compilations"][0]
    checkpoint["candidate_hash"] = audit["result_candidate_hash"] = "equal-but-not-actual-result"
    audit["audit_hash"] = _hash({k: v for k, v in audit.items() if k != "audit_hash"})
    assert not all_units_complete(db.record)


def test_source_activity_completion_keeps_authoritative_checkpoint_valid(app):
    from evograph.domain.plan_contracts import candidate_hash

    ctx, db, raw = batch(app)
    attempt = db.start_tool_attempt("submit_plan_delta", json.dumps(raw))
    result = submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    checkpoint_hash = db.record["work_units"]["checkpoints"][0]["candidate_hash"]
    assert checkpoint_hash == candidate_hash(db.project)
    db.finish_tool_attempt(attempt, "succeeded", result)
    assert checkpoint_hash != candidate_hash(db.project)
    assert all_units_complete(db.record)
    assert validate_unit_delta(db, raw) is True


@pytest.mark.parametrize("field", ["project_id", "base_candidate_hash", "result_candidate_hash", "compiled_hash"])
def test_forged_compiler_identity_rolls_back_atomic_checkpoint(app, monkeypatch, field):
    from evograph.application import plan_patch
    from evograph.application.plan_units import _hash

    ctx, db, raw = batch(app)
    original = plan_patch._completed_compiler_audit
    def forged(*args, **kwargs):
        audit = original(*args, **kwargs)
        audit[field] = "forged"
        audit["audit_hash"] = _hash({k: v for k, v in audit.items() if k != "audit_hash"})
        return audit
    monkeypatch.setattr(plan_patch, "_completed_compiler_audit", forged)
    before, record = db.project.model_dump(), deepcopy(db.record)
    with pytest.raises(ValueError, match="invalid bounded batch checkpoint"):
        submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    assert db.project.model_dump() == before
    assert db.record == record
    assert db.store.get(record["id"]) == record
    assert not all_units_complete(db.record)
