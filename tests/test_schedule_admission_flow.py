"""Offline request-routing regressions, not evidence of model design quality."""
import json

from test_agent_stream import tool_chunks
from test_unified_planning import collect, enable

HELD = {"changes": [{"kind": "slice", "id": "release", "fields": ["title", "intent", "scope"],
                      "uses": [], "intent": "Deploy the requested product"}]}
VIABLE = {"changes": [{"kind": "target", "id": "target", "fields": ["target"],
                        "uses": [], "intent": "State the requested product outcome"}]}


def fixture_provider(app, manifests):
    seen = []

    async def stream(messages, schemas):
        names = {t["function"]["name"] for t in schemas}
        seen.append({"names": names, "messages": messages})
        if "schedule_plan_changes" in names:
            args = manifests[min(sum("schedule_plan_changes" in x["names"] for x in seen) - 1,
                                 len(manifests) - 1)]
            async for event in tool_chunks("schedule_plan_changes", args):
                yield event
        else:
            # End the mock after routing is established. No design or review is fabricated.
            yield {"type": "text", "text": "Offline routing fixture ended"}

    app.settings.stream = stream
    return seen


def test_held_schedule_spends_only_one_bounded_router_repair(app):
    project = enable(app)
    calls = fixture_provider(app, [HELD])
    collect(app, project, "Deliver a small local product")
    record = app.unified.store.latest(project.id)
    assert len(calls) == 2
    assert all("schedule_plan_changes" in call["names"] for call in calls)
    assert record["generation_pause_reason"] == "schedule_admission_blocked"
    assert record["schedule_repair_requests"] == 1
    assert record["generation_progress"]["checkpoint_count"] == 0
    assert any(f["code"] == "work_units_held" for f in record["report"]["findings"])
    assert "new slice needs its first covering/owned contract" in json.dumps(calls[1]["messages"])
    assert app.db.get(project.id) == project


def test_repaired_schedule_enters_unit_generation_only_after_viable_replacement(app):
    project = enable(app)
    calls = fixture_provider(app, [HELD, VIABLE])
    collect(app, project, "Deliver a small local product")
    record = app.unified.store.latest(project.id)
    assert len(calls) == 3
    assert "schedule_plan_changes" in calls[0]["names"]
    assert "schedule_plan_changes" in calls[1]["names"]
    assert calls[2]["names"] == {"submit_plan_delta", "ask_user"}
    assert record["schedule_repair_requests"] == 1
    assert record["schedule_admission_findings"] == []
    assert record["resolved_schedule_findings"]
    assert record["generation_progress"]["checkpoint_count"] == 0
    assert app.db.get(project.id) == project


def test_malformed_manifest_repair_is_bounded_and_never_dispatches_unit(app):
    project = enable(app)
    calls = fixture_provider(app, [{"changes": []}])
    collect(app, project, "Deliver a small local product")
    record = app.unified.store.latest(project.id)
    assert len(calls) == 2
    assert all("schedule_plan_changes" in call["names"] for call in calls)
    assert record["generation_pause_reason"] == "schedule_admission_blocked"
    assert record["generation_progress"]["checkpoint_count"] == 0
    assert app.db.get(project.id) == project


def test_held_same_input_resume_routes_to_manifest_instead_of_unit(app):
    project = enable(app)
    text = "Deliver a small local product"
    fixture_provider(app, [HELD])
    collect(app, project, text)
    first = app.unified.store.latest(project.id)
    calls = fixture_provider(app, [HELD])
    collect(app, project, text)
    second = app.unified.store.latest(project.id)
    assert second["resumes_candidate_id"] == first["id"]
    assert len(calls) == 1
    assert "schedule_plan_changes" in calls[0]["names"]
    assert second["generation_progress"]["checkpoint_count"] == 0
    assert second["generation_pause_reason"] == "schedule_admission_blocked"
    assert app.db.get(project.id) == project


def test_later_viable_manifest_in_same_response_does_not_spend_router_repair(app):
    project = enable(app)
    seen = []

    async def stream(messages, schemas):
        names = {t["function"]["name"] for t in schemas}
        seen.append(names)
        if len(seen) == 1:
            for index, args in enumerate(({"changes": []}, VIABLE)):
                yield {"type": "tool_delta", "index": index, "id": f"call-{index}",
                       "name": "schedule_plan_changes", "arguments": json.dumps(args)}
        else:
            yield {"type": "text", "text": "Offline routing fixture ended"}

    app.settings.stream = stream
    collect(app, project, "Deliver a small local product")
    record = app.unified.store.latest(project.id)
    assert len(seen) == 2 and seen[1] == {"submit_plan_delta", "ask_user"}
    assert not record.get("schedule_repair_requests")
    assert record["generation_progress"]["checkpoint_count"] == 0
    assert app.db.get(project.id) == project
