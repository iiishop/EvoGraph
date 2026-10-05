"""Focused read-only continuation projection tests; no model or fixture secrets."""
from copy import deepcopy

import pytest
from evograph.application.plan_continuation_context import prior_pending_intent_context

PROJECT = "bike-shop"
ORIGIN = "repair-source"
INTENT = "SQLite 单写者事务内检查区间占用，保留提交键和预约版本"


def change(identity, fields, intent=INTENT, kind="component"):
    return {"change_id": kind + ":" + identity, "kind": kind, "id": identity,
            "fields": fields, "uses": [], "intent": intent}


def unit(number, changes, state="pending"):
    return {"id": f"unit-{number:03}", "hash": f"unit-hash-{number}", "state": state,
            "contract_count": 0, "existing_text_bytes": 40, "estimated_text_bytes": 80,
            "depends_on": [], "holds": ["fixture hold"] if state == "held" else [],
            "changes": changes}


@pytest.fixture
def record():
    atom = change("architecture", ["technologies"], kind="architecture")
    atom.update(change_id="architecture:architecture#technologies",
                origin_change_id="architecture:architecture", origin_change_hash="origin-hash")
    atom["uses"] = [{"field": "technologies", "kind": "component", "id": "store"}]
    units = [unit(1, [change("done", ["description"])], "completed"),
             unit(2, [change("store", ["description"])]),
             unit(3, [atom], "held"),
             unit(4, [change("scope", ["scope"], INTENT + "。", "slice")])]
    schedule = {"version": "plan-units/v2", "project_id": PROJECT, "source_id": ORIGIN,
                "origin_source_id": ORIGIN, "manifest_hash": "manifest-hash",
                "expected_revision": 8, "expected_fingerprint": "prior-fingerprint",
                "units": units, "completed_ids": ["unit-001"],
                "pending_ids": ["unit-002", "unit-003", "unit-004"],
                "checkpoints": [{"unit_id": "unit-001", "unit_hash": "unit-hash-1"}],
                "input_text": "Repair the current design", "manifest": [],
                "ignored_full_object": {"mechanism": "Do not replay full objects"}}
    return {"id": "continuation", "project_id": PROJECT, "resumes_candidate_id": ORIGIN,
            "input": "Keep completed work; finish remaining fixes", "prior_work_units": schedule}


def decoded(projection):
    if projection is None:
        return {}
    result = {}
    for u in projection["units"]:
        if "changes" not in u:
            continue
        result[u["id"]] = deepcopy(u["changes"])
        for c in result[u["id"]]:
            if "intent_ref" in c:
                c["intent"] = projection["intent_texts"][c.pop("intent_ref")]
    return result


def complete_current(record, completed):
    current = deepcopy(record["prior_work_units"])
    current.update(source_id=record["id"], expected_revision=9,
                   expected_fingerprint="current-fingerprint")
    for u in current["units"]:
        if u["id"] in completed:
            u["state"] = "completed"
            current["completed_ids"].append(u["id"])
            current["pending_ids"].remove(u["id"])
            current["checkpoints"].append({"unit_id": u["id"], "unit_hash": u["hash"]})
    record["work_units"] = current
    return current


def test_exact_pending_and_held_atoms_with_provenance_and_literal_dedup(record):
    before = deepcopy(record)
    projected = prior_pending_intent_context(record, PROJECT)
    prior = record["prior_work_units"]
    assert decoded(projected) == {u["id"]: u["changes"] for u in prior["units"][1:]}
    assert projected["intent_texts"] == [INTENT]
    assert projected["units"][1]["changes"][0]["intent_ref"] == 0
    assert projected["units"][2]["changes"][0]["intent_ref"] == 0
    assert projected["units"][3]["changes"][0]["intent"] == INTENT + "。"
    for key in ("version", "project_id", "source_id", "origin_source_id", "manifest_hash",
                "expected_revision", "expected_fingerprint"):
        assert projected[key] == prior[key]
    assert "changes" not in projected["units"][0]
    assert not {"manifest", "input_text", "ignored_full_object", "checkpoints"} & projected.keys()
    assert "not binding requirements" in projected["pending_changes_note"]
    assert "latest user direction" in projected["pending_changes_note"]
    projected["units"][1]["changes"][0]["fields"].append("label")
    projected["units"][2]["changes"][0]["uses"].clear()
    projected["intent_texts"].clear()
    assert record == before


def test_same_manifest_current_checkpoint_excludes_only_completed_prior_atom(record):
    complete_current(record, ["unit-002"])
    before = deepcopy(record)
    projected = prior_pending_intent_context(record, PROJECT)
    assert list(decoded(projected)) == ["unit-003", "unit-004"]
    assert projected["pending_ids"] == ["unit-002", "unit-003", "unit-004"]
    assert projected["completed_in_current"]["unit_ids"] == ["unit-002"]
    assert projected["completed_in_current"]["source_id"] == record["id"]
    assert projected["completed_in_current"]["expected_fingerprint"] == "current-fingerprint"
    assert record == before


@pytest.mark.parametrize("mismatch", ["project", "source", "origin", "manifest", "unit_hash",
                                       "checkpoint_hash", "missing_checkpoint", "missing_completed_id",
                                       "pending_state"])
def test_unrelated_or_unproved_current_completion_cannot_suppress_intent(record, mismatch):
    current = complete_current(record, ["unit-002"])
    row = current["units"][1]
    if mismatch in {"project", "source", "origin", "manifest"}:
        field = {"project": "project_id", "source": "source_id", "origin": "origin_source_id",
                 "manifest": "manifest_hash"}[mismatch]
        current[field] = "different"
    elif mismatch == "unit_hash":
        row["hash"] = "different"
        current["checkpoints"][-1]["unit_hash"] = "different"
    elif mismatch == "checkpoint_hash":
        current["checkpoints"][-1]["unit_hash"] = "different"
    elif mismatch == "missing_checkpoint":
        current["checkpoints"].pop()
    elif mismatch == "missing_completed_id":
        current["completed_ids"].remove("unit-002")
    else:
        row["state"] = "pending"
    assert "unit-002" in decoded(prior_pending_intent_context(record, PROJECT))


def test_all_matching_current_completions_add_no_pending_projection(record):
    complete_current(record, ["unit-002", "unit-003", "unit-004"])
    before = deepcopy(record)
    assert prior_pending_intent_context(record, PROJECT) is None
    assert record == before


def test_next_turn_uses_immediate_complete_predecessor_not_older_pending_ancestor(record):
    complete_current(record, ["unit-002", "unit-003", "unit-004"])
    # The existing resume path copies predecessor.work_units, not its prior_work_units.
    next_record = {"id": "later", "resumes_candidate_id": record["id"],
                   "prior_work_units": deepcopy(record["work_units"]),
                   "older_prior_snapshot": deepcopy(record["prior_work_units"])}
    assert next_record["older_prior_snapshot"]["pending_ids"]
    assert prior_pending_intent_context(next_record, PROJECT) is None


@pytest.mark.parametrize("missing", ["prior", "wrong_project", "wrong_source"])
def test_missing_or_foreign_predecessor_does_not_invent_context(record, missing):
    if missing == "prior":
        record.pop("prior_work_units")
    elif missing == "wrong_project":
        record["prior_work_units"]["project_id"] = "foreign"
    else:
        record["resumes_candidate_id"] = "foreign"
    assert prior_pending_intent_context(record, PROJECT) is None


def test_latest_direction_and_atom_ids_are_data_not_automatic_requirements(record):
    record["input"] = "Drop the optional architecture enrichment; keep the database repair"
    before = deepcopy(record)
    projection = prior_pending_intent_context(record, PROJECT)
    assert len(decoded(projection)) == 3  # The router reconciles; helper cannot interpret direction.
    assert record == before
    assert not {"requirements", "work_units", "manifest"} & projection.keys()


def test_router_opt_in_leaves_default_and_current_unit_contexts_unchanged(app, record):
    from evograph.application.plan_units import current_unit_context, initial_unit_context
    from test_plan_patch import stage

    _, db = stage(app)
    db.record["prior_work_units"] = deepcopy(record["prior_work_units"])
    db.record["prior_work_units"]["project_id"] = db.project.id
    db.record["resumes_candidate_id"] = ORIGIN
    before = deepcopy(db.record)
    default = initial_unit_context(db)
    routed = initial_unit_context(db, include_prior_pending=True)
    assert not any("changes" in u for u in default["prior_work_units"]["units"])
    assert decoded(routed["prior_work_units"])
    assert {k: v for k, v in default.items() if k != "prior_work_units"} == {
        k: v for k, v in routed.items() if k != "prior_work_units"}
    assert current_unit_context(db)["prior_work_units"] == default["prior_work_units"]
    assert db.record == before


def test_fresh_facade_and_routing_repair_both_explicitly_opt_in(app, monkeypatch):
    from evograph.application import unified_planning
    from test_agent_stream import tool_chunks
    from test_unified_planning import collect, enable

    project = enable(app)
    calls = []
    initial = unified_planning.initial_unit_context

    def capture(db, **kwargs):
        calls.append(kwargs)
        return initial(db, **kwargs)

    monkeypatch.setattr(unified_planning, "initial_unit_context", capture)
    requests = []

    async def stream(messages, schemas):
        requests.append([s["function"]["name"] for s in schemas])
        if len(requests) == 1:
            # An owner without an acceptance contract induces one routing repair.
            async for event in tool_chunks("schedule_plan_changes", {"changes": [
                {"kind": "slice", "id": "incomplete", "fields": ["title", "intent", "scope"],
                 "uses": [], "intent": "An unfinished delivery requires its own contract"}]}):
                yield event
        else:
            yield {"type": "text", "text": "No additional change proposed"}

    app.settings.stream = stream
    collect(app, project)
    assert len(calls) >= 2
    assert all(c == {"include_prior_pending": True} for c in calls)
    assert "schedule_plan_changes" in requests[0] and "schedule_plan_changes" in requests[1]
