"""Offline prompt projection checks, not a claim of model repair quality."""
import json
from copy import deepcopy

import pytest
from evograph.application.plan_harness import _semantic_result, seal_snapshot
from evograph.application.plan_repair_context import auto_repair_context, build_repair_agenda
from evograph.application.plan_review import (
    BatchSemanticReview,
    batch_review_certificate,
    batch_review_packet,
    normalize_batch_review,
)
from evograph.domain.models import Project
from evograph.domain.plan_harness import (
    CURRENT_POLICY,
    POLICY_HASH,
    HarnessFinding,
    HarnessRun,
    PluginExecution,
    PluginResult,
    execution_satisfied,
)


def execution(snapshot, plugin_id, verdict="pass", findings=(), **kwargs):
    return PluginExecution(plugin_id=plugin_id, plugin_version="1", kind="deterministic",
        status="completed", result=PluginResult(plugin_id=plugin_id, plugin_version="1",
            snapshot_id=snapshot.snapshot_id, scope="fixture", covered_subjects=("fixture",),
            verdict=verdict, findings=findings), **kwargs)


def reviewed_run(snapshot):
    packet = batch_review_packet(snapshot.before_project(), snapshot.candidate_project(),
                                 snapshot.record_data())
    subjects = packet["required_subjects"][:2]
    batch = BatchSemanticReview.model_validate({
        "candidate_hash": snapshot.candidate_hash, "review_scope_hash": packet["review_scope_hash"],
        "summary": "Offline grouped-issue fixture", "statuses": {
            "supported": packet["required_subjects"][2:], "unknown": subjects, "contradicted": []},
        "issues": [{"id": "one-shared-boundary", "subjects": subjects, "verdict": "unknown",
                    "reason": "Exact boundary is not established", "counterexample": "A retry races with commit",
                    "evidence_refs": ["/current_input"]}]})
    normalized = normalize_batch_review(snapshot.candidate_project(), packet, batch)
    certificate = batch_review_certificate(batch.model_dump_json(), batch)
    result, review_json, certificate_json = _semantic_result(snapshot, normalized, certificate)
    advisory = HarnessFinding(code="architecture_risks", subject="architecture",
                              message="Optional metadata", severity="review")
    return HarnessRun(run_id="fixture-run", snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id, candidate_hash=snapshot.candidate_hash,
        policy_version=CURRENT_POLICY.version, policy_hash=POLICY_HASH, registry_hash="fixture-registry",
        status="completed", executions=(
            execution(snapshot, "design_consistency", findings=(advisory,)),
            PluginExecution(plugin_id="semantic_review", plugin_version="1", kind="model_opinion",
                            status="completed", result=result, certificate_id="issue-certificate",
                            request_refs=("/metrics/calls/1",))),
        semantic_review_json=review_json, model_certificate_json=certificate_json)


@pytest.fixture
def sealed():
    project = Project(id="fixture-project", name="Repair context", target_draft="Preserve source intent")
    return seal_snapshot(project, project, {"id": "fixture-candidate", "base_revision": 0,
                                           "input": "Keep source intent"})


def test_grouped_semantic_issue_is_exact_once_with_sealed_provenance(sealed):
    run = reviewed_run(sealed)
    before = run.model_dump_json(), sealed.model_dump_json()
    agenda = build_repair_agenda(run, sealed)
    assert [p["plugin_id"] for p in agenda["plugins"]] == ["semantic_review"]
    plugin = agenda["plugins"][0]
    assert plugin["issues"] == json.loads(run.model_certificate_json)["batch"]["issues"]
    assert len(plugin["issues"]) == 1 and len(plugin["issues"][0]["subjects"]) == 2
    assert plugin["request_refs"] == ["/metrics/calls/1"]
    for field in ("run_id", "snapshot_id", "candidate_id", "candidate_hash", "policy_version",
                  "policy_hash", "registry_hash"):
        assert agenda[field] == getattr(run, field)
    assert "architecture_risks" not in json.dumps(agenda)
    assert "批量模型未报告" not in json.dumps(agenda, ensure_ascii=False)
    assert execution_satisfied(run.executions[0]) and not execution_satisfied(run.executions[1])
    plugin["issues"][0]["subjects"].clear()
    assert (run.model_dump_json(), sealed.model_dump_json()) == before


def test_required_deterministic_errors_dedup_without_promoting_optional_findings(sealed):
    error = HarnessFinding(code="broken_reference", subject="contract:a", message="Exact missing owner")
    run = reviewed_run(sealed).model_copy(update={"executions": (
        execution(sealed, "graph_identity", "block", (error, error)),
        execution(sealed, "optional_plugin", "block", (error,)))})
    agenda = build_repair_agenda(run, sealed)
    assert [p["plugin_id"] for p in agenda["plugins"]] == ["graph_identity"]
    issue, = agenda["plugins"][0]["issues"]
    assert {k: v for k, v in issue.items() if k != "id"} == error.model_dump(mode="json")
    assert issue["id"].startswith("graph_identity:")


def test_stale_identity_and_transport_failure_cannot_be_repair_agendas(sealed):
    run = reviewed_run(sealed)
    for field in ("snapshot_id", "candidate_id", "candidate_hash", "policy_version", "policy_hash"):
        with pytest.raises(ValueError, match="matching sealed run"):
            build_repair_agenda(run.model_copy(update={field: "stale"}), sealed)
    failed = PluginExecution(plugin_id="semantic_review", plugin_version="1", kind="model_opinion",
                             status="unavailable", detail="Retry transport")
    with pytest.raises(ValueError, match="controlled retry"):
        build_repair_agenda(run.model_copy(update={"executions": (failed,)}), sealed)


def test_auto_scope_removes_old_tasks_but_preserves_saved_facts_and_completed_units(sealed):
    agenda = build_repair_agenda(reviewed_run(sealed), sealed)
    context = {"candidate_hash": sealed.candidate_hash, "sources": [{"text": "Exact intent"}],
               "findings": [{"code": "advisory"}], "inherited_findings": [{"code": "resolved"}],
               "inherited_semantic_findings": {"issues": [{"id": "already-fixed-staff"}]},
               "findings_note": "old", "prior_work_units": {"pending_ids": ["old-unit"]},
               "schedule": {"pending_ids": ["new-unit"]}, "current_unit": {"id": "new-unit"},
               "completed_unit_facts": [{"unit_id": "done-unit"}]}
    before = deepcopy(context)
    assert auto_repair_context(context, None) is context  # Ordinary generation is untouched.
    projected = auto_repair_context(context, agenda)
    assert projected["repair_agenda"]["applicability"] == "current_reviewed_snapshot"
    for key in ("sources", "schedule", "current_unit", "completed_unit_facts"):
        assert projected[key] == context[key]
    assert not {"findings", "inherited_findings", "inherited_semantic_findings", "findings_note",
                "prior_work_units"} & projected.keys()
    changed = auto_repair_context({**context, "candidate_hash": "after-checkpoint"}, agenda)
    assert changed["repair_agenda"]["applicability"] == "historical_needs_recheck"
    assert changed["repair_agenda"]["candidate_hash"] == sealed.candidate_hash
    projected["repair_agenda"]["plugins"].clear()
    assert agenda["plugins"] and context == before


def test_resumed_generation_resets_auto_repair_router_and_keeps_unit_feedback(app, monkeypatch):
    from evograph.application import unified_planning
    from test_agent_stream import tool_chunks
    from test_schedule_admission_flow import VIABLE, fixture_provider
    from test_unified_planning import collect, enable

    project = enable(app)
    text = "State the requested product outcome"
    fixture_provider(app, [VIABLE])
    collect(app, project, text)  # Retain a routed, unfinished target unit.
    seen = []

    async def harness(snapshot, evaluator, checkpoint):
        run = reviewed_run(snapshot)
        checkpoint(run)
        return run

    async def stream(messages, schemas):
        names = {s["function"]["name"] for s in schemas}
        seen.append((names, deepcopy(messages)))
        if len(seen) == 1:
            assert "submit_plan_delta" in names  # Resume entered the old unit callback.
            async for event in tool_chunks("submit_plan_delta", {"target": text}):
                yield event
        elif "schedule_plan_changes" in names:
            async for event in tool_chunks("schedule_plan_changes", VIABLE):
                yield event
        else:
            yield {"type": "text", "text": "Stop offline fixture after repair unit context"}

    monkeypatch.setattr(unified_planning, "run_harness", harness)
    app.settings.stream = stream
    collect(app, project, text)
    assert len(seen) == 3, app.unified.store.latest(project.id)["report"]
    assert seen[1][0] == {"schedule_plan_changes", "ask_user"}
    router = json.loads(seen[1][1][0]["content"].split("\nCurrent state (data):\n")[1])
    unit = json.loads(seen[2][1][0]["content"].split(
        "\nCurrent saved candidate and assigned work unit (data):\n")[1])
    assert "current_unit" not in router and unit["current_unit"]["id"]
    assert router["repair_agenda"]["applicability"] == "current_reviewed_snapshot"
    assert unit["repair_agenda"]["applicability"] == "historical_needs_recheck"
    assert {k: v for k, v in router["repair_agenda"].items() if k != "applicability"} == {
        k: v for k, v in unit["repair_agenda"].items() if k != "applicability"}
    for data in (router, unit):
        assert "prior_work_units" not in data and "inherited_findings" not in data
        assert len(data["repair_agenda"]["plugins"][0]["issues"]) == 1
    saved = app.unified.store.latest(project.id)
    assert saved["report"]["findings"][0]["code"] == "architecture_risks"
    assert saved["report"]["semantic"] and saved["report"]["semantic_batch"]
    assert saved["prior_work_units"]  # Prompt filtering never erases the audit.
    assert app.db.get(project.id) == project
