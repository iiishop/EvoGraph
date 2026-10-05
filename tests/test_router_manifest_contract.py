"""Offline frozen-output rejection and lossless router-context proofs, not model QA."""
import asyncio
import gzip
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from evograph.application.plan_budget import compact_schema, encoded_size
from evograph.application.plan_continuation_context import prior_pending_intent_context
from evograph.application.plan_units import (
    MANIFEST_TOOL,
    SchedulePlanChanges,
    _manifest_rows,
    current_unit_context,
    initial_unit_context,
)
from evograph.application.tool_execution import ToolExecutor, _plan_manifest_input_failure
from evograph.domain.models import Project
from pydantic import ValidationError
from test_bounded_planning_job import collect, request
from test_bounded_planning_job import seven as seven
from test_plan_patch import stage

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def frozen():
    provenance = json.loads((FIXTURES / "qa55-invalid-router-provenance.json").read_text())
    expected = provenance["fixture"]
    raw = gzip.decompress((FIXTURES / expected["compressed_file"]).read_bytes())
    assert len(raw) == expected["bytes"] and hashlib.sha256(raw).hexdigest() == expected["sha256"]
    record = json.loads(raw)
    assert hashlib.sha256(record["tool_attempts"][0]["raw_arguments"].encode()).hexdigest() == provenance["raw_arguments_sha256"]
    assert hashlib.sha256(json.dumps(record["model_inputs"][0], ensure_ascii=False, sort_keys=True).encode()).hexdigest() == provenance["frozen_request_sha256"]
    return record


def diagnostic(arguments):
    with pytest.raises(ValidationError) as raised:
        SchedulePlanChanges.model_validate_json(arguments)
    return _plan_manifest_input_failure(MANIFEST_TOOL, arguments, raised.value)


def ungroup(projection):
    """Recover every original pending atom, including field-level intent/uses."""
    result = {}
    for unit in projection["units"]:
        if "changes" not in unit:
            continue
        rows = []
        for row in deepcopy(unit["changes"]):
            if "pending_atom_ids" not in row:
                rows.append(row)
                continue
            field_intents = {item["field"]: item for item in row.get("field_intents", [])}
            for field, identity in zip(row["fields"], row["pending_atom_ids"], strict=True):
                atom = {key: row[key] for key in ("kind", "id", "origin_change_id", "origin_change_hash")}
                intent = field_intents.get(field, row)
                atom.update(change_id=identity, fields=[field],
                            uses=[u for u in row["uses"] if u["field"] == field],
                            **{key: intent[key] for key in ("intent", "intent_ref") if key in intent})
                rows.append(atom)
        for row in rows:
            if "intent_ref" in row:
                row["intent"] = projection["intent_texts"][row.pop("intent_ref")]
        result[unit["id"]] = rows
    return result


def test_actual_wire_shape_is_unchanged_and_explicitly_describes_one_row(frozen):
    wire = deepcopy(frozen["model_inputs"][0]["tools"][0]["function"]["parameters"])
    schema = compact_schema(SchedulePlanChanges.model_json_schema())
    description = schema["properties"]["changes"].pop("description")
    assert schema == wire
    assert "One row per (kind,id)" in description
    assert "combine all changed field names" in description
    for branch in schema["properties"]["changes"]["items"]["anyOf"]:
        assert branch["additionalProperties"] is False
        assert set(branch["properties"]) == {"kind", "id", "fields", "intent", "uses"}
    raw = frozen["tool_attempts"][0]["raw_arguments"]
    with pytest.raises(ValidationError) as raised:
        SchedulePlanChanges.model_validate_json(raw)
    errors = raised.value.errors(include_input=False, include_context=False, include_url=False)
    assert len(errors) == 20 and {e["type"] for e in errors} == {"extra_forbidden"}
    assert {(e["loc"][1], e["loc"][2]) for e in errors} == {
        (i, field) for i in range(8) for field in (["quote", "source_id"] if i < 4 else ["quote", "rule", "source_id"])}


def test_actual_rejection_diagnostics_group_all_errors_and_duplicate_locations(frozen):
    raw = frozen["tool_attempts"][0]["raw_arguments"]
    original = deepcopy(frozen)
    result = diagnostic(raw)
    payload = result["payload"]
    assert result["progress"] is False and payload["ok"] is False
    assert payload["code"] == "invalid_plan_manifest"
    assert {(tuple(i["path"]), i["type"], i["count"]) for i in payload["schema_issues"]} == {
        (("changes", "*", "quote"), "extra_forbidden", 8),
        (("changes", "*", "source_id"), "extra_forbidden", 8),
        (("changes", "*", "rule"), "extra_forbidden", 4)}
    assert payload["omitted_issue_count"] == 0
    assert payload["identity_issues"] == [{"type": "duplicate_identity", "locations": [["changes", i] for i in range(19, 23)]}]
    assert encoded_size(payload) < 2000
    assert frozen == original
    assert "raw_arguments" not in json.dumps(result)


def test_actual_invalid_output_rejected_before_handler_with_exact_raw_audit(app, frozen):
    ctx, db = stage(app)
    before, canonical, saved_events = db.project.model_dump(), app.db.get(ctx.project_id), deepcopy(db.saved_events)
    calls = []
    tool = replace(MANIFEST_TOOL, handler=lambda *args: calls.append(args))
    executor = ToolExecutor(ctx, {tool.name: tool})
    raw = frozen["tool_attempts"][0]["raw_arguments"]
    result = asyncio.run(executor.invoke(tool.name, raw))
    assert calls == [] and not executor.changed and executor.calls_used == 1
    assert db.record["tool_attempts"][-1]["raw_arguments"] == raw
    assert db.record["tool_attempts"][-1]["status"] == "failed"
    assert db.record["tool_attempts"][-1]["payload"] == result["payload"]
    assert not {"work_units", "unit_request", "compilations", "tool_calls"} & db.record.keys()
    # Only the required rejected-attempt activity audit can change the candidate.
    current = db.project.model_dump()
    for source in current["plan_contract"]["sources"]:
        source["activity"] = []
    assert current == before and db.saved_events == saved_events
    assert app.db.get(ctx.project_id) == canonical


@pytest.mark.parametrize("kind,ids,fields", [
    ("architecture", ["architecture", "architecture"], ["risks"]),
    ("contract", ["private-contract", "private-contract"], ["statement"]),
    ("relation", ['["a","b","calls"]', '[ "a", "b", "calls" ]'], ["source", "target", "label"]),
])
def test_duplicate_identities_rejected_before_handler_and_existing_gate_kept(app, kind, ids, fields):
    rows = [{"kind": kind, "id": identity, "fields": fields, "uses": [], "intent": "Intent"} for identity in ids]
    ctx, _ = stage(app)
    entered = []
    tool = replace(MANIFEST_TOOL, handler=lambda *args: entered.append(args))
    result = asyncio.run(ToolExecutor(ctx, {tool.name: tool}).invoke(tool.name, json.dumps({"changes": rows})))
    assert entered == [] and result["payload"]["identity_issues"] == [
        {"type": "duplicate_identity", "locations": [["changes", 0], ["changes", 1]]}]
    # The scheduler's pre-existing gate is still present, even for a constructed model.
    individually_valid = [SchedulePlanChanges.model_validate({"changes": [row]}).changes[0] for row in rows]
    with pytest.raises(ValueError, match="duplicate identity"):
        _manifest_rows(SchedulePlanChanges.model_construct(changes=individually_valid))


@pytest.mark.parametrize("raw", [
    '{"changes":[{"secret-property-name":"secret-value"}]}',
    '{"changes":[{"kind":"secret-value","id":"secret-value","fields":[],"intent":"secret-value"}]}',
    '{"changes": "secret-value"}', '{"secret-property-name":"secret-value"}',
    '{"changes": ["secret-value",', '"secret-value"',
])
def test_diagnostics_never_echo_values_unknown_keys_or_json_excerpts(raw):
    result = diagnostic(raw)
    assert "secret" not in json.dumps(result) and not result["payload"]["ok"]
    assert all(set(issue) == {"path", "type", "count", "locations"} for issue in result["payload"]["schema_issues"])


def test_diagnostic_bounds_cover_huge_invalid_lists_without_echoing_values():
    row = {"kind": "requirement", "id": "private-value", "fields": ["quote"], "intent": "Keep",
           **{f"private-key-{i}": "private-value" for i in range(60)}}
    result = diagnostic(json.dumps({"changes": [row] * 150}))
    payload = result["payload"]
    assert payload["identity_scan_omitted_rows"] == 22
    assert len(payload["schema_issues"]) <= 12
    assert all(len(group["locations"]) <= 12 for group in payload["schema_issues"])
    assert encoded_size(payload) < 15000 and "private" not in json.dumps(result)


def test_actual_projection_roundtrips_atoms_preserves_facts_and_generator_context(frozen):
    before = deepcopy(frozen)
    project = Project.model_validate(frozen["project"])
    db = SimpleNamespace(project=project, record=frozen, source_id=frozen["id"], get=lambda _: project)
    default = initial_unit_context(db)
    routed = initial_unit_context(db, include_prior_pending=True)
    prior = routed["prior_work_units"]
    expected = {u["id"]: u["changes"] for u in frozen["prior_work_units"]["units"]
                if u["id"] in frozen["prior_work_units"]["pending_ids"] and u["state"] != "completed"}
    assert ungroup(prior) == expected
    architecture = [r for u in prior["units"] for r in u.get("changes", []) if r["kind"] == "architecture"]
    assert len(architecture) == 1 and len(architecture[0]["pending_atom_ids"]) == 4
    assert {k: v for k, v in routed.items() if k != "prior_work_units"} == {
        k: v for k, v in default.items() if k != "prior_work_units"}
    assert current_unit_context(db)["prior_work_units"] == default["prior_work_units"]
    current_staff = next(b for b in project.behaviors if b.id == "0e504c09ee764b15")
    staff = next(b for b in routed["acceptance_directory"] if b["key"] == "contract-staff-console")
    mechanism = next(b for b in routed["completed_contract_mechanisms"] if b["key"] == staff["key"])
    saved_mechanism = next(b for b in project.plan_contract.bindings if b.behavior_key == staff["key"])
    assert staff["statement"] == current_staff.statement and "并在取消提交返回后异步投递" in staff["statement"]
    assert mechanism["mechanism"] == saved_mechanism.mechanism and "待发送候补通知记录" in mechanism["mechanism"]
    assert routed["slices"] == [m.model_dump(include={"id", "title", "intent", "scope", "dependencies"}) for m in project.milestones]
    assert len(routed["slices"]) == 7
    assert routed["requirements"] == project.plan_contract.model_dump()["requirements"]
    source_text = {s.id: s.text for s in project.plan_contract.sources}
    for source in routed["sources"]:
        assert source_text[source["id"]] == (source.get("text") or source_text[source["same_text_as_source_id"]])
    assert set(routed["sources"][0]) == set(default["sources"][0])
    prior["units"][-1]["changes"][0]["fields"].append("consumer-only-mutation")
    assert frozen == before


@pytest.mark.parametrize("mutation", ["different_intent", "different_origin", "unknown_atom_key",
    "duplicate_field", "interleaved", "null_uses", "missing_use_field", "nonmapping_use", "unhashable_origin"])
def test_grouping_preserves_distinct_field_intents_and_leaves_unrecognized_shapes(frozen, mutation):
    prior = frozen["prior_work_units"]
    unit = next(u for u in prior["units"] if sum(r["kind"] == "architecture" for r in u["changes"]) == 4)
    rows = unit["changes"]
    if mutation == "different_intent":
        rows[-1]["intent"] = "Distinct exact technology field intent"
    elif mutation == "different_origin":
        rows[-1]["origin_change_hash"] = "different-origin"
    elif mutation == "unknown_atom_key":
        rows[-1]["unknown"] = "Preserve new provenance exactly"
    elif mutation == "duplicate_field":
        rows.append(deepcopy(rows[-1]))
    elif mutation == "null_uses":
        rows[0]["uses"] = None
    elif mutation == "missing_use_field":
        rows[0]["uses"] = [{"kind": "component", "id": "store"}]
    elif mutation == "nonmapping_use":
        rows[0]["uses"] = ["Keep the malformed historical value"]
    elif mutation == "unhashable_origin":
        rows[0]["origin_change_hash"] = ["Keep the malformed historical value"]
    else:
        rows.insert(1, {"change_id": "contract:interleaved", "kind": "contract", "id": "interleaved", "fields": ["statement"], "uses": [], "intent": "Keep placement"})
    before = deepcopy(frozen)
    projected = prior_pending_intent_context(frozen, frozen["project_id"])
    expected = {u["id"]: u["changes"] for u in prior["units"] if u["id"] in prior["pending_ids"] and u["state"] != "completed"}
    assert ungroup(projected) == expected and frozen == before
    if mutation == "different_intent":
        grouped = next(u for u in projected["units"] if u["id"] == unit["id"])["changes"][0]
        assert len(grouped["field_intents"]) == 4


def test_first_router_failure_stops_bounded_job_without_retry_or_checkpoint(app, seven, frozen):
    calls = []
    raw = frozen["tool_attempts"][0]["raw_arguments"]
    async def stream(messages, schemas, **kwargs):
        calls.append(1)
        yield {"type": "tool_delta", "index": 0, "id": "invalid-frozen", "name": "schedule_plan_changes", "arguments": raw}
    app.settings.stream = stream
    events = collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    job = app.planning_jobs.store.get("offline-job")
    assert calls == [1] and job["status"] == "stopped" and job["stop_reason"] == "invalid_tool_output"
    assert not app.projects.get(seven.id)["planning_job"]["can_continue"] and len(job["phases"]) == 1
    assert record["tool_attempts"][0]["raw_arguments"] == raw
    assert record["tool_attempts"][0]["payload"]["code"] == "invalid_plan_manifest"
    assert not record.get("work_units") and not record.get("compilations") and not record.get("reviews")
    assert record["generation_progress"]["checkpoint_count"] == 0
    assert any(e["type"] == "tool_failed" and e["code"] == "invalid_plan_manifest" for e in events)
    assert app.db.get(seven.id) == seven


def test_nonrouter_input_failure_path_is_unchanged(app):
    from evograph.agent_tools import tools
    ctx, _ = stage(app)
    name = "ask_user"
    result = asyncio.run(ToolExecutor(ctx, tools()).invoke(name, '{}'))
    assert result["events"][0]["code"] == "tool_failed"
    assert set(result["payload"]) == {"ok", "error"}
