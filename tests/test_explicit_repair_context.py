"""Offline explicit-stage prompt/provenance checks, not model repair quality."""
import json
from copy import deepcopy

import pytest
from evograph.application.plan_batch_policy import REPAIR_EXPERIMENT
from evograph.application.plan_repair_context import REPAIR_INSTRUCTIONS, build_repair_agenda
from evograph.application.plan_review import resolve_packet_pointer
from evograph.domain.plan_harness import CURRENT_POLICY, HarnessRun, HarnessSnapshot, content_hash
from test_agent_stream import tool_chunks
from test_architecture_unit_bootstrap import manifest_for, valid_delta
from test_bounded_cold_start import collect_experiment
from test_review_materiality import batch_for
from test_unified_planning import enable


def held_review(packet):
    issues = [{"id": identity, "subjects": [subject], "verdict": "unknown",
        "reason": "An exact source boundary needs a decision", "counterexample": counterexample,
        "evidence_refs": ["/current_input"], "materiality": {
            "obligation_ref": "/current_input", "obligation_excerpt": resolve_packet_pointer(packet, "/current_input"),
            "affected_owner_ids": ["CHECK"], "gap_kind": "unresolved_semantics",
            "boundary_refs": ["/candidate/plan_contract/bindings/0/mechanism"],
            "necessary_plan_change": counterexample}}
        for identity, subject, counterexample in (
            ("read-boundary", "cross_contract_consistency", "Define unreadable file handling"),
            ("path-boundary", "failure_boundaries", "Define invalid path handling"))]
    observations = [{"id": "wording-advisory", "subjects": ["cross_contract_consistency"],
        "kind": "editorial", "reason": "Optional concise wording", "basis": "source_statement",
        "evidence_refs": ["/current_input"]}]
    return batch_for(packet, issues=issues, observations=observations).model_dump()


@pytest.fixture
def held(app):
    project = enable(app)
    async def stream(messages, schemas):
        names = {s["function"]["name"] for s in schemas}
        raw = valid_delta(app.unified.store.latest(project.id)["id"])
        raw["risks"] = []  # A satisfied deterministic plugin's advisory is not a repair task.
        if "schedule_plan_changes" in names:
            name, args = "schedule_plan_changes", {"changes": manifest_for(raw)}
        elif "submit_plan_delta" in names:
            name, args = "submit_plan_delta", raw
        else:
            name, args = "submit_plan_review", held_review(json.loads(messages[-1]["content"]))
        async for event in tool_chunks(name, args):
            yield event
    app.settings.stream = stream
    collect_experiment(app, project)
    record = app.unified.store.latest(project.id)
    assert record["metrics"]["provider_calls"] == 3
    assert record["status"] == "needs_resolution" and record["harness_run"]["decision"] == "hold"
    assert record["report"].get("semantic_batch", {}).get("observations"), record["harness_run"]["executions"][-1]
    assert any(f["code"] == "architecture_risks" for f in record["report"]["findings"])
    return project, record


def test_explicit_repair_router_and_unit_get_only_exact_blockers(app, held):
    project, previous = held
    frozen = deepcopy(previous)
    seen = []
    delta = {"contracts": [{"key": "links", "mechanism":
        "Traverse Markdown read-only; report unreadable files and invalid paths without writing files"}]}
    async def stream(messages, schemas):
        names = {s["function"]["name"] for s in schemas}
        seen.append((names, deepcopy(messages)))
        if "schedule_plan_changes" in names:
            name, args = "schedule_plan_changes", {"changes": manifest_for(delta)}
        elif "submit_plan_delta" in names:
            name, args = "submit_plan_delta", delta
        else:
            name, args = "submit_plan_review", held_review(json.loads(messages[-1]["content"]))
        async for event in tool_chunks(name, args):
            yield event
    app.settings.stream = stream
    collect_experiment(app, project, REPAIR_EXPERIMENT)
    saved = app.unified.store.latest(project.id)
    assert len(seen) == 3, saved["report"]
    assert seen[0][0] == {"schedule_plan_changes", "ask_user"}
    assert seen[1][0] == {"submit_plan_delta", "ask_user"}
    assert seen[2][0] == {"submit_plan_review"}
    contexts = [json.loads(seen[i][1][0]["content"].split(marker)[1]) for i, marker in (
        (0, "\nCurrent state (data):\n"),
        (1, "\nCurrent saved candidate and assigned work unit (data):\n"))]
    for i, context in enumerate(contexts):
        agenda = context["repair_agenda"]
        assert REPAIR_INSTRUCTIONS in seen[i][1][0]["content"]
        assert agenda["applicability"] == "historical_needs_recheck"
        assert agenda["run_id"] == previous["harness_run"]["run_id"]
        assert agenda["snapshot_id"] == previous["harness_run"]["snapshot_id"]
        assert agenda["candidate_hash"] == previous["candidate_hash"] != context["candidate_hash"]
        assert [p["plugin_id"] for p in agenda["plugins"]] == ["semantic_review"]
        assert agenda["plugins"][0]["issues"] == previous["report"]["semantic_batch"]["issues"]
        assert not {"findings", "inherited_findings", "inherited_semantic_findings",
                    "findings_note", "prior_work_units"} & context.keys()
        encoded = json.dumps(context, ensure_ascii=False)
        assert "wording-advisory" not in encoded and "architecture_risks" not in encoded
        assert "批量模型未报告" not in encoded
        assert set(saved["allowed_requirement_source_ids"]) == {previous["id"], saved["id"]}
    assert contexts[1]["current_unit"]["id"]
    assert saved["metrics"]["provider_calls"] == 3 and saved["metrics"]["budget"]["max_calls"] == 5
    assert saved["metrics"]["planning_experiment"]["limits"]["max_calls"] == 3
    assert len(saved["work_units"]["units"]) == len(saved["work_units"]["checkpoints"]) == 1
    assert saved["generation_progress"]["checkpoint_count"] == 2
    assert saved["generation_pause_reason"] == "experiment_review_complete"
    assert saved["prior_work_units"] == previous["work_units"]
    assert saved["inherited_findings"] == previous["report"]["findings"]
    assert app.unified.store.get(previous["id"]) == frozen
    assert app.db.get(project.id) == project


@pytest.mark.parametrize("damage", ["missing_snapshot", "different_snapshot", "duplicate_snapshot",
    "changed_snapshot", "raw_certificate", "audit_certificate", "certificate_id", "policy", "registry"])
def test_invalid_explicit_repair_stops_before_provider_and_candidate(app, held, damage):
    project, record = held
    if damage == "missing_snapshot":
        record.pop("harness_snapshots")
    elif damage == "different_snapshot":
        record["harness_run"]["snapshot_id"] = "not-the-sealed-snapshot"
    elif damage == "duplicate_snapshot":
        record["harness_snapshots"] *= 2
    elif damage == "changed_snapshot":
        record["harness_snapshots"][0]["candidate_json"] += " "
    elif damage == "raw_certificate":
        certificate = json.loads(record["harness_run"]["model_certificate_json"])
        certificate["raw_arguments"] += " "  # Same parsed batch still fails its exact certificate ID.
        record["harness_run"]["model_certificate_json"] = json.dumps(certificate)
        record["batch_reviews"][-1] = certificate
    elif damage == "audit_certificate":
        record["batch_reviews"][-1]["raw_arguments"] += " "
    elif damage == "certificate_id":
        record["harness_run"]["executions"][-1]["certificate_id"] = "invalid"
    else:
        record["harness_run"][damage + "_hash"] = "obsolete"
    record = app.unified.store.save(record)
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Invalid provenance must never dispatch a provider")
        yield
    app.settings.stream = forbidden
    events = collect_experiment(app, project, REPAIR_EXPERIMENT)
    assert any(e["type"] == "error" for e in events)
    assert calls == []
    assert app.unified.store.latest(project.id) == record
    assert app.db.get(project.id) == project


def test_optional_semantic_issues_never_become_required_repair(held):
    _, record = held
    optional = CURRENT_POLICY.model_copy(update={"version": "fixture-optional",
                                                 "require_model_opinion": False})
    run = HarnessRun.model_validate_json(json.dumps(record["harness_run"])).model_copy(update={
        "policy_version": optional.version, "policy_hash": content_hash(optional.model_dump(mode="json"))})
    snapshot = HarnessSnapshot.model_validate_json(json.dumps(record["harness_snapshots"][0]))
    assert build_repair_agenda(run, snapshot, policy=optional)["plugins"] == []
