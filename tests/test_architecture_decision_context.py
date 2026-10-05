"""Saved design choices reach every unit; context is not semantic validation."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from evograph.application.plan_budget import BudgetedSettings, BudgetExceededError
from evograph.application.plan_stage import StagedDatabase
from evograph.application.plan_units import (
    _architecture_context,
    initial_unit_context,
    prepare_unit_request,
    resume_unit_schedule,
)
from evograph.domain.plan_contracts import IntentSource
from test_architecture_unit_bootstrap import schedule
from test_plan_patch import INPUT, architecture, initial, patch, stage


@pytest.fixture
def saved_architecture(app):
    ctx, db = stage(app)
    spec = architecture(
        "core", "store",
        summary="One small server for a six-technician repair shop; no online payments.",
        technologies=[{"area": "database", "choice": "SQLite", "rationale": "One local database"}],
        decisions=["Use SQLite transactions and fixed-slot uniqueness; no PostgreSQL EXCLUDE."],
        risks=["Concurrent bookings must not reserve the same technician and slot."],
        quality_scenarios=[{"concern": "booking conflict", "scenario": "Two simultaneous bookings",
                            "measure": "Only one succeeds", "approach": "Unique occupied slots"}],
    )
    spec["diagram"]["groups"] = [
        {"id": "server", "label": "Single server", "kind": "server",
         "description": "Application and database run on the same small server",
         "member_node_ids": ["core", "store"]},
    ]
    spec["diagram"]["milestone_ids"] = ["CHECK"]
    initial(ctx, architecture=spec)
    return ctx, db


def schedule_contract(ctx):
    schedule(ctx, [{"kind": "contract", "id": "check", "fields": ["statement"],
                    "uses": [], "intent": "Clarify the acceptance statement"}])


def metadata_of(architecture):
    expected = deepcopy(architecture)
    for key in ("number", "created_at"):
        expected.pop(key)
    for key in ("nodes", "edges"):
        expected["diagram"].pop(key)
    return expected


def test_nonarchitecture_unit_receives_exact_saved_choices_and_metadata(saved_architecture):
    ctx, db = saved_architecture
    schedule_contract(ctx)
    context = prepare_unit_request(db)
    saved = db.project.architectures[-1].model_dump()
    facts = context["architecture_context"]
    assert "architecture:architecture" not in context["current_unit"]["objects"]
    assert facts["status"] == "saved"
    assert facts["number"] == saved["number"] and facts["created_at"] == saved["created_at"]
    assert facts["metadata"] == metadata_of(saved)
    assert facts["metadata"]["technologies"][0]["choice"] == "SQLite"
    assert facts["metadata"]["decisions"] == saved["decisions"]
    assert facts["metadata"]["diagram"]["groups"] == saved["diagram"]["groups"]
    assert "not proof of semantic compatibility" in facts["note"]
    assert "nodes" not in facts["metadata"]["diagram"]
    assert "edges" not in facts["metadata"]["diagram"]


def test_new_router_and_resumed_unit_read_latest_saved_revision(app, saved_architecture):
    ctx, db = saved_architecture
    old = initial_unit_context(db)["architecture_context"]
    revised = db.project.architectures[-1].model_dump(exclude={"number", "created_at"})
    revised["decisions"] = ["Latest exact decision: BEGIN IMMEDIATE before checking occupied slots."]
    patch(ctx, architecture=revised)
    schedule_contract(ctx)
    latest = initial_unit_context(db)["architecture_context"]
    assert latest["number"] == old["number"] + 1
    assert latest["metadata"]["decisions"] == revised["decisions"]

    candidate = db.get(db.project.id)
    candidate.plan_contract.sources.append(IntentSource(id="resume", text=INPUT))
    resumed_schedule = resume_unit_schedule(db.record, candidate, "resume", INPUT)
    assert resumed_schedule is not None
    record = {**deepcopy(db.record), "id": "resume", "turn_id": "resume",
              "project": candidate.model_dump(), "work_units": resumed_schedule, "unit_request": None}
    resumed = StagedDatabase(app.db, app.unified.store, record, "resume")
    assert prepare_unit_request(resumed)["architecture_context"] == latest
    assert initial_unit_context(resumed)["architecture_context"] == latest

    _, fresh = stage(app, db.project, text="Add a staff scheduling view")
    assert "work_units" not in fresh.record
    assert prepare_unit_request(fresh)["architecture_context"] == latest


@pytest.mark.parametrize("full_object", [False, True])
def test_context_mutation_cannot_change_saved_architecture(saved_architecture, full_object):
    ctx, db = saved_architecture
    if full_object:
        schedule(ctx, [{"kind": "architecture", "id": "architecture", "fields": ["decisions"],
                        "uses": [], "intent": "Clarify the decision"}])
    else:
        schedule_contract(ctx)
    context = prepare_unit_request(db)
    before_project, before_record = db.project.model_dump(), deepcopy(db.record)
    facts = (context["current_unit"]["objects"]["architecture:architecture"] if full_object
             else context["architecture_context"]["metadata"])
    facts["technologies"][0]["choice"] = "Changed only in consumer context"
    facts["decisions"].append("Not a saved decision")
    facts["diagram"]["groups"][0]["member_node_ids"].clear()
    assert db.project.model_dump() == before_project
    assert db.record == before_record
    assert db.store.get(db.record["id"]) == before_record


def test_full_current_architecture_is_referenced_without_metadata_duplication(saved_architecture):
    ctx, db = saved_architecture
    schedule(ctx, [{"kind": "architecture", "id": "architecture", "fields": ["decisions"],
                    "uses": [], "intent": "Clarify the decision"}])
    context = prepare_unit_request(db)
    facts = context["architecture_context"]
    saved = db.project.architectures[-1].model_dump()
    assert facts["value_ref"] == "current_unit.objects['architecture:architecture']"
    assert "metadata" not in facts
    assert facts["number"] == saved["number"] and facts["created_at"] == saved["created_at"]
    assert context["current_unit"]["objects"]["architecture:architecture"] == saved
    assert "all architecture fields exactly" in facts["coverage"]
    # An incomplete or different revision cannot justify replacing exact metadata.
    for incomplete in ({"number": saved["number"]}, {**saved, "number": saved["number"] - 1}):
        fallback = _architecture_context(saved, incomplete)
        assert "value_ref" not in fallback
        assert fallback["metadata"] == metadata_of(saved)


def test_absent_architecture_is_explicit_without_invented_defaults(app):
    _, db = stage(app)
    for context in (initial_unit_context(db), prepare_unit_request(db)):
        assert context["architecture_context"] == {
            "status": "absent", "note": "No saved architecture exists in this candidate."}


def test_complete_metadata_is_not_truncated_to_evade_existing_request_cap(saved_architecture):
    _, db = saved_architecture
    risk = "Exact saved risk: " + "x" * 100_000
    db.project.architectures[-1].risks = [risk]
    context = initial_unit_context(db)
    assert context["architecture_context"]["metadata"]["risks"] == [risk]
    metrics = {"provider_calls": 0}
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
    assert metrics["admission_rejections"][0]["request_limit_bytes"] == 98304
