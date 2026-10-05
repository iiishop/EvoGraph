"""Projection and shared guidance regressions, not natural-language entailment tests."""
import asyncio
import gzip
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from evograph.application.plan_budget import BudgetedSettings, BudgetExceededError
from evograph.application.plan_units import (
    BATCH_UNIT_VERSION,
    _compact_unit_directories,
    _objects,
    initial_unit_context,
    prepare_unit_request,
    schedule_findings,
)
from evograph.application.unified_planning import CHANGE_PRESERVATION, ROUTER, generator_prompt
from evograph.domain.models import Project
from test_architecture_unit_bootstrap import schedule
from test_plan_patch import initial, node, stage


@pytest.mark.parametrize("record", [{}, {"work_units": {"version": BATCH_UNIT_VERSION}}])
def test_router_and_generator_share_preservation_and_classification_guidance(record):
    for prompt in (ROUTER, generator_prompt(record)):
        assert prompt.count(CHANGE_PRESERVATION) == 1
        assert "Reuse existing requirement IDs" in prompt
        assert "reclassify an existing requirement to bypass coverage" in prompt
        assert "without moving an early promise to a later-only contract" in prompt
        assert "do not rewrite an unchanged target, title, intent or scope for cleanup" in prompt
        assert "independent full checks remain required" in prompt


def test_saved_r7_router_keeps_staff_acceptance_mechanism_and_delivery_boundaries_exact():
    fixture = Path(__file__).parent / "fixtures/qa55-closed-repair-candidate.json.gz"
    record = json.loads(gzip.decompress(fixture.read_bytes()))
    project = Project.model_validate(record["project"])
    # A new repair starts from these completed units, not the canonical R2 plan.
    record["prior_work_units"] = deepcopy(record["work_units"])
    before_project, before_record = project.model_dump(), deepcopy(record)
    db = SimpleNamespace(project=project, record=record, source_id=record["id"],
                         get=lambda identity: project)
    context = initial_unit_context(db)
    objects = _objects(project)
    saved = objects["contract:contract-staff-console"]
    staff = next(row for row in context["acceptance_directory"] if row["key"] == saved["key"])
    mechanism = next(row for row in context["completed_contract_mechanisms"] if row["key"] == saved["key"])
    assert context["revision"] == 7 and saved["revision_id"] == "0e504c09ee764b15"
    assert staff == {key: value for key, value in saved.items() if key != "mechanism"}
    assert mechanism == {key: saved[key] for key in ("key", "revision_id", "mechanism")}
    assert "并在取消提交返回后异步投递" in staff["statement"]
    assert "待发送候补通知记录" in mechanism["mechanism"]
    for milestone in project.milestones:
        row = next(row for row in context["slices"] if row["id"] == milestone.id)
        assert row == milestone.model_dump(include={"id", "title", "intent", "scope", "dependencies"})
    assert context["requirements"] == before_project["plan_contract"]["requirements"]
    assert context["sources"]  # Complete source text/alias behavior is unchanged.
    context["slices"][0]["scope"].append("Consumer-only mutation")
    assert project.model_dump() == before_project
    assert record == before_record


def test_full_assigned_slice_deduplicates_only_matching_directory_facts(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node("CHECK", scope=["Preserve exact early-stage work"]),
                             node("OTHER", scope=["Unrelated scope stays readable"])])
    schedule(ctx, [{"kind": "slice", "id": "CHECK", "fields": ["intent"],
                    "uses": [], "intent": "Clarify the existing delivery intent"}])
    context = prepare_unit_request(db)
    full = context["current_unit"]["objects"]["slice:CHECK"]
    directory = {row["id"]: row for row in context["slices"]}
    assert directory["CHECK"] == {key: full[key] for key in ("id", "title", "dependencies")}
    assert full["scope"] == ["Preserve exact early-stage work"]
    assert directory["OTHER"]["scope"] == ["Unrelated scope stays readable"]
    assert "slice:CHECK" in context["slices_note"]
    assert "current_unit.objects" in context["slices_note"]


@pytest.mark.parametrize("field", ["id", "title", "intent", "scope", "dependencies"])
def test_mismatched_full_slice_never_suppresses_directory_boundary(field):
    row = {"id": "CHECK", "title": "Check", "intent": "Saved intent", "scope": ["Saved scope"],
           "dependencies": ["BASE"]}
    full = {**deepcopy(row), field: ["Different"] if isinstance(row[field], list) else "Different"}
    context = {"current_unit": {"objects": {"slice:CHECK": full}},
               "slices": [deepcopy(row)], "acceptance_directory": [], "components": []}
    _compact_unit_directories(context)
    assert context["slices"] == [row]
    assert "slices_note" not in context


def test_uncovered_requirement_stays_held_without_reclassification(app):
    ctx, db = stage(app)
    initial(ctx)
    before = db.project.model_dump()
    schedule(ctx, [{"kind": "requirement", "id": "req-early-acceptance-not-deferred",
                    "fields": ["kind", "quote"], "uses": [],
                    "intent": "No consumer was provided; preserve the coverage hold"}])
    assert db.record["work_units"]["units"][0]["holds"] == [
        "new requirement needs its first covering/owned contract"]
    assert schedule_findings(db.record, db.project)
    assert prepare_unit_request(db)["current_unit"] is None
    assert db.project.model_dump() == before
    assert db.record["work_units"]["manifest"][0]["kind"] == "requirement"


def test_large_delivery_boundary_is_exact_and_request_budget_still_blocks(app):
    ctx, db = stage(app)
    initial(ctx)
    scope = "Exact saved delivery boundary " + "x" * 170_000
    db.project.milestones[0].scope = [scope]
    context = initial_unit_context(db)
    assert context["slices"][0]["scope"] == [scope]
    metrics = {"provider_calls": 0, "budget": {"max_request_bytes": 163840}}
    invoked = []

    async def stream(messages, tools):
        invoked.append(True)
        yield {"type": "text", "text": "Must never be sent"}

    async def request():
        settings = BudgetedSettings(SimpleNamespace(stream=stream, secrets=None), metrics)
        async for _ in settings.stream([{"role": "system", "content": json.dumps(context)}], []):
            pass

    with pytest.raises(BudgetExceededError):
        asyncio.run(request())
    assert invoked == [] and metrics["provider_calls"] == 0
    assert metrics["admission_rejections"][0]["reason"] == "request_input_limit"
    assert metrics["admission_rejections"][0]["request_limit_bytes"] == 163840


def test_completed_contract_ids_survive_a_new_held_manifest_but_text_is_current():
    from evograph.application.plan_units import retained_unit_schedule_history

    fixture = Path(__file__).parent / "fixtures/qa55-closed-repair-candidate.json.gz"
    complete = json.loads(gzip.decompress(fixture.read_bytes()))
    project = Project.model_validate(complete["project"])
    old_schedule = deepcopy(complete["work_units"])
    held = deepcopy(old_schedule)
    held.update(completed_ids=[], checkpoints=[], pending_ids=[unit["id"] for unit in held["units"]])
    for unit in held["units"]:
        unit.update(state="held", holds=["new requirement needs its first covering/owned contract"])
    predecessor = {**deepcopy(complete), "prior_work_units": old_schedule, "work_units": held}
    frozen = deepcopy(predecessor)
    record = {"id": "new-source", "prior_work_units": deepcopy(held),
              "work_unit_schedule_history": retained_unit_schedule_history(predecessor)}
    assert record["prior_work_units"] == held
    assert record["work_unit_schedule_history"] == [old_schedule]
    assert retained_unit_schedule_history({**predecessor,
        "work_unit_schedule_history": record["work_unit_schedule_history"]}) == [old_schedule]
    current = next(binding for binding in project.plan_contract.bindings
                   if binding.behavior_key == "contract-staff-console")
    old_mechanism = current.mechanism
    current.mechanism = "CURRENT staged mechanism, never text copied from an old checkpoint"
    db = SimpleNamespace(project=project, record=record, source_id=record["id"],
                         get=lambda identity: project)
    context = initial_unit_context(db)
    staff = next(row for row in context["completed_contract_mechanisms"]
                 if row["key"] == "contract-staff-console")
    assert staff["mechanism"] == current.mechanism != old_mechanism
    assert staff["revision_id"] == current.behavior_revision_id
    assert context["requirements"] == project.plan_contract.model_dump()["requirements"]
    assert record["prior_work_units"] == held and predecessor == frozen
