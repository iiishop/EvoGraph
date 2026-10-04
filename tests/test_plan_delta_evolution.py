"""Offline evolution of the frozen qa49 plan, never evidence of model quality.

All generation/review responses are hand-authored local fixtures. The historical
long-leading-zero parsing defect is deliberately not repaired, executed, or
presented as newly discovered here. No test accesses its original profile or DB.
"""

import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from evograph.application.plan_budget import encoded_size
from evograph.application.plan_ir import compile_plan_delta
from evograph.domain.models import Project
from evograph.domain.plan_contracts import (
    active_behaviors,
    candidate_hash,
    contract_findings,
    history_findings,
    planning_payload,
)
from test_agent_stream import tool_chunks

FIXTURES = Path(__file__).parent / "fixtures"
FROZEN = json.loads((FIXTURES / "qa49_frozen_plan.json").read_text())
EVOLUTION = json.loads((FIXTURES / "qa49_evolution_deltas.json").read_text())
BATCH_INPUT = EVOLUTION["batch_input"]
RESTORE_INPUT = EVOLUTION["restore_input"]
ORIGINAL_IDS = {"req-arg-contract", "req-success-stdout", "req-error-path"}
BATCH_IDS = {"req-batch-arguments", "req-batch-stdout", "req-batch-errors"}
RESTORE_IDS = {"req-single-v2-arguments", "req-single-v2-stdout", "req-single-v2-errors"}
STABLE_KEYS = {"convert-formula", "core-no-io-purity", "cli-stdlib-and-io-boundary"}
CHANGED_KEYS = {"format-two-decimals", "argv-validation", "success-output-exit", "failure-output-exit"}


@pytest.fixture
def frozen():
    return Project.model_validate(deepcopy(FROZEN["project"]))


@pytest.fixture
def installed(app, frozen):
    # Only the temporary app fixture database is initialized from the frozen JSON.
    app.db.create(frozen)
    assert app.db.get(frozen.id) == frozen
    return frozen


def delta(name="batch"):
    return deepcopy(EVOLUTION[f"{name}_delta"])


def without_sources(project):
    result = planning_payload(project)
    result["plan_contract"].pop("sources")
    return result


def fixture_review(packet, *, stale_hash=None, verdict="supported"):
    return {
        "candidate_hash": stale_hash or packet["candidate_hash"],
        "summary": "Offline hand-authored workflow fixture, not a quality evaluation",
        "checks": [{
            "subject": subject, "verdict": verdict,
            "reason": "Fixture exercises exact-subject/hash gating only; no semantic proof is claimed",
            "counterexample": "Human review must challenge a later invalid argument after valid ones, including the unchanged long-leading-zero parsing limitation",
        } for subject in packet["required_subjects"]],
    }


class OfflineResponses:
    def __init__(self, app, payload, *, stale_hash=None, verdict="supported", on_review=None):
        self.payload = deepcopy(payload)
        self.stale_hash, self.verdict, self.on_review = stale_hash, verdict, on_review
        self.requests, self.packets = [], []
        self.generations = 0
        app.settings.stream = self.stream

    async def stream(self, messages, schemas):
        self.requests.append(deepcopy({"messages": messages, "tools": schemas}))
        names = [schema["function"]["name"] for schema in schemas]
        if names == ["submit_plan_review"]:
            packet = json.loads(messages[-1]["content"])
            self.packets.append(packet)
            if self.on_review:
                self.on_review(packet)
            async for event in tool_chunks("submit_plan_review", fixture_review(
                packet, stale_hash=self.stale_hash, verdict=self.verdict,
            )):
                yield event
            return
        assert "submit_plan_delta" in names
        self.generations += 1
        if self.generations == 1:
            async for event in tool_chunks("submit_plan_delta", self.payload):
                yield event
        else:
            # Do not fabricate an automatic repair after a rejected fixture review.
            yield {"type": "text", "text": "离线fixture不声称已解决语义评审问题"}


def run_turn(app, project, payload, content=BATCH_INPUT, **options):
    responder = OfflineResponses(app, payload, **options)
    before = app.db.get(project.id)
    previews = []

    async def collect():
        events = []
        async for event in app.agent.stream(project.id, content, snapshot_mode="compact-v1"):
            events.append(event)
            if event["type"] == "candidate_changed" and event["candidate"]["status"] not in {"applied", "stale"}:
                previews.append(deepcopy(event["candidate"]))
                if not options.get("on_review"):
                    assert app.db.get(project.id) == before
        return events

    events = asyncio.run(collect())
    record = app.unified.store.latest(project.id)
    return app.db.get(project.id), record, events, responder, previews


def assert_history_preserved(before, after):
    assert after.behaviors[:len(before.behaviors)] == before.behaviors
    assert after.targets[:len(before.targets)] == before.targets
    assert after.architectures[:len(before.architectures)] == before.architectures
    assert after.plan_contract.sources[:len(before.plan_contract.sources)] == before.plan_contract.sources
    assert after.plan_contract.process_constraints == before.plan_contract.process_constraints
    assert history_findings(before, after) == contract_findings(after) == []
    old, new = active_behaviors(before), active_behaviors(after)
    assert old.keys() == new.keys()
    for key in STABLE_KEYS:
        assert new[key] == old[key]
    for key in CHANGED_KEYS:
        assert new[key].behavior_key == old[key].behavior_key
        assert new[key].id != old[key].id
        assert new[key].version > old[key].version
    assert [m.id for m in after.milestones] == [m.id for m in before.milestones]
    assert [n.id for n in after.architectures[-1].diagram.nodes] == [
        n.id for n in before.architectures[-1].diagram.nodes
    ]
    assert after.architectures[-1].diagram.nodes[0] == before.architectures[-1].diagram.nodes[0]
    assert after.architectures[-1].risks == before.architectures[-1].risks
    assert after.architectures[-1].diagram.edges == before.architectures[-1].diagram.edges
    assert after.milestone("slice-02-cli").dependencies == ["slice-01-core"]
    assert after.milestone("slice-01-core").scope == before.milestone("slice-01-core").scope


def assert_retired_against_new_source(before, after, retired_ids, added_ids, text):
    old = {r.id: r for r in before.plan_contract.requirements}
    current = {r.id: r for r in after.plan_contract.requirements}
    source = after.plan_contract.sources[-1]
    assert source.text == text
    assert source.id not in {s.id for s in before.plan_contract.sources}
    assert set(current) == set(old) | added_ids
    for rid, previous in old.items():
        assert current[rid].quote == previous.quote
        assert current[rid].source_id == previous.source_id
        if rid in retired_ids:
            assert not current[rid].active
            assert current[rid].retired_by.source_id == source.id
            assert current[rid].retired_by.quote in text
        else:
            assert current[rid] == previous
    for rid in added_ids:
        assert current[rid].active
        assert current[rid].source_id == source.id
        assert current[rid].quote in text
    assert not retired_ids & {rid for b in after.plan_contract.bindings for rid in b.requirement_ids}


def test_frozen_fixture_has_exact_project_integrity_and_preserves_known_limitation(frozen):
    encoded = json.dumps(FROZEN["project"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == FROZEN["provenance"]["project_sha256"]
    assert frozen.model_dump(mode="json") == FROZEN["project"]
    assert frozen.name == "qa49-flat" and not frozen.repository
    assert len(frozen.milestones) == 2 and len(frozen.behaviors) == 7
    bindings = {b.behavior_key: b for b in frozen.plan_contract.bindings}
    assert "int(s, 10)" in bindings["argv-validation"].mechanism
    assert "int(s, 10)" in next(c for c in delta()["contracts"] if c["key"] == "argv-validation")["mechanism"]
    assert "long-leading-zero" in FROZEN["provenance"]["limitation"]


def test_batch_evolution_preserves_unrelated_ids_and_publishes_once_after_review(app, installed):
    before = installed
    after, record, events, responses, previews = run_turn(app, before, delta())
    assert events[-1]["changed"] and record["status"] == "applied"
    assert after.revision == before.revision + 1
    assert len(after.targets) == len(before.targets) + 1
    assert len(after.architectures) == len(before.architectures) + 1
    assert len(responses.requests) == 2 and len(responses.packets) == 1
    assert len([p for p in previews if p["status"] == "reviewing"]) == 1
    assert_history_preserved(before, after)
    assert_retired_against_new_source(before, after, ORIGINAL_IDS, BATCH_IDS, BATCH_INPUT)
    assert record["report"]["semantic"]["candidate_hash"] == candidate_hash(Project.model_validate(record["project"]))
    assert record["compilations"][0]["ir"] == delta()
    assert len(record["compilations"]) == 1
    active = active_behaviors(after)
    assert "多个有效参数不再是错误" in active["argv-validation"].statement
    assert "前面存在有效参数也不能输出部分结果" in active["failure-output-exit"].statement
    assert after.milestone("slice-02-cli").intent == delta()["slices"][0]["intent"]
    assert after.architectures[-1].diagram.nodes[1].description == delta()["components"][0]["description"]
    assert after.architectures[-1].technologies[3].choice.endswith("<c> [<c> ...] 方式运行")
    assert not record["metrics"]["usage_reported"]  # No invented real-provider tokens/cost.
    assert len(app.db.messages(after.id)) == 1


def test_explicit_batch_reversal_uses_new_identities_and_keeps_both_retirement_histories(app, installed):
    batch, first, *_ = run_turn(app, installed, delta())
    restored, second, events, responses, _ = run_turn(app, batch, delta("restore"), RESTORE_INPUT)
    assert first["status"] == second["status"] == "applied" and events[-1]["changed"]
    assert len(responses.requests) == 2
    assert_history_preserved(batch, restored)
    assert_retired_against_new_source(batch, restored, BATCH_IDS, RESTORE_IDS, RESTORE_INPUT)
    reqs = {r.id: r for r in restored.plan_contract.requirements}
    batch_reqs = {r.id: r for r in batch.plan_contract.requirements}
    for rid in ORIGINAL_IDS:
        assert reqs[rid] == batch_reqs[rid] and not reqs[rid].active
    assert not (ORIGINAL_IDS | BATCH_IDS) & {r.id for r in reqs.values() if r.active}
    assert len(restored.targets) == 3 and len(restored.architectures) == 3
    assert [s.text for s in restored.plan_contract.sources] == [
        installed.plan_contract.sources[0].text, BATCH_INPUT, RESTORE_INPUT,
    ]
    assert restored.targets[-1].statement == installed.targets[-1].statement
    for key in STABLE_KEYS:
        assert active_behaviors(restored)[key].id == active_behaviors(installed)[key].id
    for key in CHANGED_KEYS:
        assert active_behaviors(restored)[key].statement == active_behaviors(installed)[key].statement
    assert restored.architectures[-1].summary == installed.architectures[-1].summary
    assert len(app.db.messages(restored.id)) == 2


def test_read_only_noop_does_not_make_new_target_history_or_review_request(app, installed):
    current, record, events, responses, _ = run_turn(app, installed, {}, "保持现有规划，不修改任何契约。")
    assert current == installed
    assert not events[-1]["changed"]
    assert record["status"] == "applied"
    assert record["compilations"][0]["ir"] == {}
    assert len(responses.requests) == 1 and not responses.packets
    assert Project.model_validate(record["project"]) == installed


@pytest.mark.parametrize("fault", ["unknown_requirement", "invented_quote", "old_id_rewrite"])
def test_invalid_delta_never_leaves_partial_slices_architecture_or_retirements(app, installed, fault):
    payload = delta()
    if fault == "unknown_requirement":
        payload["contracts"][1]["requirement_ids"] = ["unknown"]
    elif fault == "invented_quote":
        # Source validation occurs after private graph and architecture compilation.
        payload["requirements"][0]["quote"] = "This sentence is not user input"
    else:
        payload["requirements"][0]["id"] = "req-formula"
    current, record, events, responses, _ = run_turn(app, installed, payload)
    assert current == installed
    assert record["status"] == "failed" and not events[-1]["changed"]
    assert without_sources(Project.model_validate(record["project"])) == without_sources(installed)
    assert not record.get("compilations")
    assert not responses.packets
    assert any(event["type"] == "tool_failed" for event in events)
    assert record["tool_attempts"][0]["status"] == "failed"


def test_reversal_cannot_reactivate_a_retired_identity_or_rewrite_its_old_quote(app, installed):
    batch, *_ = run_turn(app, installed, delta())
    payload = delta("restore")
    payload["requirements"][0]["id"] = "req-arg-contract"
    for contract in payload["contracts"]:
        contract["requirement_ids"] = ["req-arg-contract" if rid == "req-single-v2-arguments" else rid
                                       for rid in contract["requirement_ids"]]
    current, record, events, responses, _ = run_turn(app, batch, payload, RESTORE_INPUT)
    assert current == batch and not events[-1]["changed"]
    assert without_sources(Project.model_validate(record["project"])) == without_sources(batch)
    assert not record.get("compilations") and not responses.packets
    assert not next(r for r in current.plan_contract.requirements if r.id == "req-arg-contract").active


def test_stale_semantic_hash_cannot_publish_the_evolved_candidate(app, installed):
    current, record, events, responses, _ = run_turn(app, installed, delta(), stale_hash=candidate_hash(installed))
    assert current == installed and not events[-1]["changed"]
    assert record["status"] == "needs_resolution"
    assert len(responses.packets) == 1
    assert responses.packets[0]["candidate_hash"] != candidate_hash(installed)
    assert any(f["code"] == "review_unavailable" for f in record["report"]["findings"])
    assert without_sources(Project.model_validate(record["project"])) != without_sources(installed)


@pytest.mark.parametrize("verdict", ["unknown", "contradicted"])
def test_non_support_fixture_verdict_preserves_canonical_without_claiming_quality(app, installed, verdict):
    current, record, events, responses, _ = run_turn(app, installed, delta(), verdict=verdict)
    assert current == installed and not events[-1]["changed"]
    assert record["status"] == "needs_resolution"
    assert len(responses.packets) == 1  # No unchanged repair gets a second review.
    assert all(c["verdict"] == verdict for c in record["reviews"][0]["checks"])


def test_concurrent_canonical_edit_blocks_publication_of_old_base(app, installed):
    def concurrent(_packet):
        newer = app.db.get(installed.id)
        newer.description = "Concurrent canonical edit"
        app.db.save(newer, "fixture_concurrent_edit")

    current, record, events, _, _ = run_turn(app, installed, delta(), on_review=concurrent)
    assert current.description == "Concurrent canonical edit"
    assert current.revision == installed.revision + 1
    assert without_sources(current) == without_sources(installed)
    assert record["status"] == "stale" and not events[-1]["changed"]
    assert not {r.id for r in current.plan_contract.requirements} & BATCH_IDS


def test_failed_candidate_checkpoint_keeps_entire_plan_before_delta(app, installed, monkeypatch):
    save = app.unified.store.save
    failures = []

    def fail_compiled_checkpoint(record):
        if record.get("compilations") and not failures:
            failures.append(record["compilations"][0])
            raise OSError("Injected candidate checkpoint failure")
        return save(record)

    monkeypatch.setattr(app.unified.store, "save", fail_compiled_checkpoint)
    current, record, events, responses, _ = run_turn(app, installed, delta())
    assert failures and current == installed and not events[-1]["changed"]
    assert not record.get("compilations") and not responses.packets
    assert without_sources(Project.model_validate(record["project"])) == without_sources(installed)


def test_publication_sidecar_failure_rolls_back_core_and_contract_after_full_review(app, installed, monkeypatch):
    writer = app.db._write_planning_extensions
    writes = []
    with app.db.connect() as connection:
        raw_before = tuple(connection.execute("SELECT revision,payload FROM projects WHERE id=?", (installed.id,)).fetchone())

    def fail_after_extension_write(connection, project_id, extensions):
        writer(connection, project_id, extensions)
        writes.append(deepcopy(extensions))
        raise OSError("Injected after core and extension writes")

    monkeypatch.setattr(app.db, "_write_planning_extensions", fail_after_extension_write)
    current, record, events, responses, _ = run_turn(app, installed, delta())
    assert len(writes) == 1 and len(responses.packets) == 1
    assert BATCH_IDS <= {r["id"] for r in writes[0]["plan_contract"]["requirements"]}
    assert current == installed and not events[-1]["changed"]
    assert record["status"] == "failed"
    assert any(f["code"] == "publication_failed" for f in record["report"]["findings"])
    with app.db.connect() as connection:
        raw_after = tuple(connection.execute("SELECT revision,payload FROM projects WHERE id=?", (installed.id,)).fetchone())
    assert raw_after == raw_before
    assert without_sources(Project.model_validate(record["project"])) != without_sources(installed)


def test_full_source_requests_and_all_surviving_constraints_reach_review_untruncated(app, installed):
    batch, _, _, responses, _ = run_turn(app, installed, delta())
    packet = responses.packets[0]
    candidate = packet["candidate"]
    assert packet["complete"] is True
    assert packet["current_input"] == BATCH_INPUT
    assert [s["text"] for s in candidate["plan_contract"]["sources"]] == [
        installed.plan_contract.sources[0].text, BATCH_INPUT,
    ]
    assert candidate["milestones"] == [m.model_dump() for m in batch.milestones]
    assert candidate["architecture"] == batch.architectures[-1].model_dump()
    assert candidate["plan_contract"] == batch.plan_contract.model_dump()
    for rid in ORIGINAL_IDS:
        assert "retirement:" + rid in packet["required_subjects"]
    for rid in BATCH_IDS:
        assert "requirement:" + rid in packet["required_subjects"]
    assert {"req-formula", "req-mandatory-cases", "req-stdlib-only", "req-no-io"} <= {
        r["id"] for r in candidate["plan_contract"]["requirements"] if r["active"]
    }


def budget_report(responses, record, payload):
    requests = []
    for index, request in enumerate(responses.requests):
        call = record["metrics"]["calls"][index]
        actual = {"input_bytes": encoded_size(request), "schema_bytes": encoded_size(request["tools"]),
                  "message_bytes": encoded_size(request["messages"])}
        for key, value in actual.items():
            assert call[key] == value
        requests.append({"kind": "review" if request["tools"][0]["function"]["name"] == "submit_plan_review" else "generation", **actual})
    return {"delta_compact_bytes": encoded_size(payload),
            "delta_streamed_arguments_bytes": len(json.dumps(payload, ensure_ascii=False).encode()),
            "requests": requests,
            "total_input_bytes": sum(request["input_bytes"] for request in requests),
            "max_request_bytes": record["metrics"]["budget"]["max_request_bytes"],
            "max_total_input_bytes": record["metrics"]["budget"]["max_total_input_bytes"],
            "real_provider_calls": 0, "usage_reported": record["metrics"]["usage_reported"]}


def test_exact_offline_request_budgets_and_small_contract_edit_avoid_full_core_resend(app, installed):
    payload = delta()
    batch, first, _, first_responses, _ = run_turn(app, installed, payload)
    restored, second, _, second_responses, _ = run_turn(app, batch, delta("restore"), RESTORE_INPUT)
    reports = {"batch": budget_report(first_responses, first, payload),
               "restore": budget_report(second_responses, second, delta("restore"))}
    assert encoded_size(payload) == 5902
    assert encoded_size(delta("restore")) == 7600
    assert {c["key"] for c in payload["contracts"]} == CHANGED_KEYS
    assert not {c["key"] for c in payload["contracts"]} & STABLE_KEYS
    assert [s["id"] for s in payload["slices"]] == ["slice-02-cli"]
    assert len(first["compilations"][0]["ir"]["contracts"]) == 4
    assert len(installed.behaviors) == len(active_behaviors(restored)) == 7
    # A mechanism refinement is one changed contract, not a repeated core graph.
    single = {"contracts": [deepcopy(payload["contracts"][3])]}
    single["contracts"][0]["mechanism"] += "错误文字必须保持单行。"
    compiled = compile_plan_delta(batch, single)
    assert [m.id for m in compiled.patch.milestones] == ["slice-02-cli"]
    assert {b.key for b in compiled.patch.milestones[0].behaviors} == {
        "argv-validation", "success-output-exit", "failure-output-exit", "cli-stdlib-and-io-boundary",
    }
    reports["single_contract_bytes"] = encoded_size(single)
    reports["frozen_project_bytes"] = encoded_size(FROZEN["project"])
    assert reports["single_contract_bytes"] < reports["frozen_project_bytes"] / 20
    for report in (reports["batch"], reports["restore"]):
        assert report["total_input_bytes"] < report["max_total_input_bytes"]
        assert all(request["input_bytes"] < report["max_request_bytes"] for request in report["requests"])
        assert not report["usage_reported"]
    print("OFFLINE_EVOLUTION_BUDGET=" + json.dumps(reports, ensure_ascii=False, sort_keys=True))
