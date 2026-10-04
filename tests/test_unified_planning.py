"""Enforceable workflow guarantees, intentionally not an AI-quality score."""
import asyncio
import json

import pytest
from evograph.agent_tools.base import ToolContext
from evograph.application.plan_contracts import TracePlan, trace_plan
from evograph.application.plan_stage import StagedDatabase
from evograph.application.unified_planning import review_packet
from evograph.domain.models import Project, now
from evograph.domain.plan_contracts import (
    IntentSource,
    SemanticReview,
    candidate_hash,
    contract_findings,
    planning_payload,
    review_subjects,
    validate_semantic_review,
)
from evograph.infrastructure.database import ConflictError
from test_agent_stream import tool_chunks

INPUT = "Build a local link checker for all Markdown links. Do not modify source files."
NODE = {"id": "CHECK", "title": "Local checker", "intent": "Report broken local links",
        "scope": ["parser and direct-call checker"], "behaviors": [{"key": "links", "statement": "All local Markdown links are checked without source writes"}], "resources": ["parser"], "change_types": ["general"]}
TRACE = {"add_requirements": [{"id": "links", "quote": INPUT, "kind": "outcome"}],
         "bindings": [{"behavior_key": "links", "requirement_ids": ["links"], "mechanism": "Parse Markdown AST and resolve every local destination without writes"}]}


def delta_fixture(requirements=None):
    contract = {**NODE["behaviors"][0], "owner": NODE["id"],
                **{k: v for k, v in TRACE["bindings"][0].items() if k != "behavior_key"}}
    return {"target": INPUT, "requirements": requirements or TRACE["add_requirements"],
            "slices": [{k: NODE[k] for k in ("id", "title", "intent", "scope")}],
            "contracts": [contract]}


def enable(app):
    p = app.projects.create("Unified test")
    app.unified.enable(p.id, True)
    return app.db.get(p.id)


def review_for(packet, verdict="supported"):
    return {"candidate_hash": packet["candidate_hash"], "summary": "No issue found" if verdict == "supported" else "Needs resolution",
            "checks": [{"subject": subject, "verdict": verdict, "reason": "Concrete contract and ownership checked", "counterexample": "Nested link syntax is covered by the complete AST parser contract"} for subject in packet["required_subjects"]]}


def provider(app, *, verdict="supported", bad_review=False, on_review=None):
    generation = 0
    linked = json.loads(json.dumps(NODE))
    linked["behaviors"][0].update({k: v for k, v in TRACE["bindings"][0].items() if k != "behavior_key"})
    sequence = [("submit_plan_delta", delta_fixture())]
    async def stream(messages, schemas):
        nonlocal generation
        if schemas[0]["function"]["name"] == "submit_plan_review":
            if on_review:
                on_review()
            if bad_review:
                yield {"type": "text", "text": "Looks fine"}
            else:
                packet = json.loads(messages[-1]["content"])
                async for event in tool_chunks("submit_plan_review", review_for(packet, verdict)):
                    yield event
            yield {"type": "usage", "tokens": 7}
            return
        if generation < len(sequence):
            name, args = sequence[generation]
            generation += 1
            async for event in tool_chunks(name, args):
                yield event
        else:
            yield {"type": "text", "text": "候选规划已整理"}
        yield {"type": "usage", "tokens": 11}
    app.settings.stream = stream


def collect(app, p, content=INPUT, **kwargs):
    async def run():
        return [e async for e in app.agent.stream(p.id, content, **kwargs)]
    return asyncio.run(run())


def test_candidate_is_visible_early_but_canonical_is_atomic(app):
    p = enable(app)
    provider(app)
    async def run():
        previews = []
        async for event in app.agent.stream(p.id, INPUT):
            if event["type"] == "candidate_changed" and event["candidate"]["status"] != "applied":
                previews.append(event)
                assert app.db.get(p.id).milestones == []
            if event["type"] == "done":
                assert event["changed"]
        return previews
    previews = asyncio.run(run())
    assert any(e["candidate"]["project"]["milestones"] for e in previews)
    saved = app.db.get(p.id)
    assert saved.revision == p.revision + 1
    assert len(saved.milestones) == len(saved.targets) == 1
    record = app.unified.store.latest(p.id)
    assert record["status"] == "applied"
    assert record["metrics"]["provider_calls"] == 2
    assert record["metrics"]["tokens"] == 18
    assert record["candidate_hash"] == candidate_hash(Project.model_validate(record["project"]))
    assert record["report"]["semantic"]["candidate_hash"] == record["candidate_hash"]
    assert app.agent.turn_result(p.id, record["id"])["summary"]["changed"]
    assert len([m for m in app.db.messages(p.id) if m["role"] == "user"]) == 1


@pytest.mark.parametrize("verdict", ["contradicted", "unknown"])
def test_semantic_issue_retains_candidate_without_canonical_changes(app, verdict):
    p = enable(app)
    provider(app, verdict=verdict)
    events = collect(app, p)
    assert app.db.get(p.id) == p
    record = app.unified.store.latest(p.id)
    assert record["status"] == "needs_resolution"
    assert len(record["reviews"]) == 1
    assert len(record["project"]["milestones"]) == 1
    assert not events[-1]["changed"]
    assert events[-1]["summary"]["candidate_outcome"]["canonical_unchanged"]
    assert len([m for m in app.db.messages(p.id) if m["role"] == "user"]) == 1
    assert app.projects.get(p.id)["plan_candidate"]["status"] == "needs_resolution"


def test_malformed_review_is_unavailable_not_pass(app):
    p = enable(app)
    provider(app, bad_review=True)
    collect(app, p)
    record = app.unified.store.latest(p.id)
    assert record["status"] == "needs_resolution"
    assert record["report"]["findings"][0]["code"] == "review_unavailable"
    assert record["metrics"]["provider_calls"] == 2
    assert app.db.get(p.id) == p


def test_concurrent_change_blocks_commit_and_preserves_newer_state(app):
    p = enable(app)
    def concurrent():
        current = app.db.get(p.id)
        current.description = "Updated elsewhere"
        app.db.save(current, "concurrent")
    provider(app, on_review=concurrent)
    collect(app, p)
    current = app.db.get(p.id)
    assert current.description == "Updated elsewhere"
    assert not current.milestones
    assert app.unified.store.latest(p.id)["status"] == "stale"


def test_cancel_after_tool_checkpoint_keeps_canonical(app):
    p = enable(app)
    provider(app)
    async def run():
        stream = app.agent.stream(p.id, INPUT)
        async for event in stream:
            if event["type"] == "candidate_changed" and event["candidate"]["project"]["milestones"]:
                await stream.aclose()
                break
    asyncio.run(run())
    assert app.db.get(p.id) == p
    record = app.unified.store.latest(p.id)
    assert record["status"] == "stopped"
    assert record["project"]["milestones"]
    assert app.agent.turn_result(p.id, record["id"])["summary"]["status"] == "stopped"
    assert not app.agent.active_turns
    assert app.operation_lock(p.id).acquire(False)
    app.operation_lock(p.id).release()


def test_source_quotes_cannot_be_rewritten_or_invented(app):
    p = enable(app)
    p.plan_contract.sources = [IntentSource(id="turn", text=INPUT)]
    record = {"id": "turn", "project": p.model_dump(), "created_at": now(), "base_revision": p.revision,
              "revision": p.revision, "status": "generating", "report": {}, "metrics": {}}
    db = StagedDatabase(app.db, app.unified.store, record, "turn")
    ctx = ToolContext(p.id, type("Facade", (), {"db": db})())
    with pytest.raises(ValueError, match="逐字"):
        trace_plan(ctx, TracePlan(add_requirements=[{"id": "r", "quote": "Only simple links", "kind": "outcome"}]))
    trace_plan(ctx, TracePlan(add_requirements=[{"id": "r", "quote": INPUT, "kind": "outcome"}]))
    with pytest.raises(ValueError, match="不可覆盖"):
        trace_plan(ctx, TracePlan(add_requirements=[{"id": "r", "quote": "Markdown links", "kind": "outcome"}]))
    assert db.get(p.id).plan_contract.requirements[0].quote == INPUT
    assert not app.db.get(p.id).plan_contract.requirements


def completed_candidate(app):
    p = enable(app)
    provider(app, verdict="unknown")
    collect(app, p)
    record = app.unified.store.latest(p.id)
    return p, record, Project.model_validate(record["project"])


def test_binding_revision_and_declared_future_guards_are_enforced(app):
    _, _, candidate = completed_candidate(app)
    candidate.plan_contract.bindings[0].behavior_revision_id = "old"
    candidate.plan_contract.bindings[0].requires_behavior_keys = ["future_guard"]
    codes = {f["code"] for f in contract_findings(candidate)}
    assert {"stale_binding", "future_control"} <= codes


def test_uncovered_requirement_and_inactive_acceptance_are_not_clear(app):
    _, _, candidate = completed_candidate(app)
    candidate.plan_contract.bindings = []
    assert {f["code"] for f in contract_findings(candidate)} == {"untraced_acceptance", "uncovered_requirement"}


def test_review_exact_hash_and_subject_set_required(app):
    _, record, candidate = completed_candidate(app)
    packet = {"candidate_hash": candidate_hash(candidate), "required_subjects": review_subjects(candidate)}
    valid = SemanticReview.model_validate(review_for(packet))
    assert validate_semantic_review(candidate, valid)
    candidate.milestones[0].intent = "Changed after review"
    with pytest.raises(ValueError, match="过期"):
        validate_semantic_review(candidate, valid)
    valid.candidate_hash = candidate_hash(candidate)
    valid.checks.pop()
    with pytest.raises(ValueError, match="逐项"):
        validate_semantic_review(candidate, valid)


def test_no_silent_review_input_truncation(app, monkeypatch):
    p, record, candidate = completed_candidate(app)
    monkeypatch.setattr("evograph.application.unified_planning.MAX_REVIEW_BYTES", 100)
    with pytest.raises(ValueError, match="未截断"):
        review_packet(p, candidate, record)


def test_staged_write_failure_does_not_advance_in_memory_revision(app, monkeypatch):
    p = enable(app)
    record = {"id": "turn", "project": p.model_dump(), "created_at": now(), "base_revision": p.revision,
              "revision": p.revision, "status": "generating"}
    db = StagedDatabase(app.db, app.unified.store, record, "turn")
    edited = db.get(p.id)
    edited.target_draft = "Changed"
    def fail(record):
        raise OSError("fixture I/O failure")
    monkeypatch.setattr(app.unified.store, "save", fail)
    with pytest.raises(OSError):
        db.save(edited, "test")
    assert db.project == p
    assert edited.revision == p.revision


def test_candidate_cannot_modify_repository_or_evidence(app):
    p = enable(app)
    record = {"id": "turn", "project": p.model_dump(), "created_at": now(), "base_revision": p.revision,
              "revision": p.revision, "status": "generating"}
    db = StagedDatabase(app.db, app.unified.store, record, "turn")
    edited = db.get(p.id)
    edited.repository = "/unrelated"
    with pytest.raises(ValueError, match="可写范围"):
        db.save(edited, "test")
    assert db.project == p


def test_plain_legacy_projects_remain_on_existing_route(app):
    p = app.projects.create("Legacy")
    assert not p.unified_planning
    assert not app.projects.get(p.id)["plan_candidate"]
    assert planning_payload(p)["plan_contract"] == {"sources": [], "requirements": [], "bindings": [], "process_constraints": []}


def test_question_cas_never_rebases_over_concurrent_plan(app):
    p = enable(app)
    async def stream(messages, schemas):
        current = app.db.get(p.id)
        current.target_draft = "Concurrent requirement"
        app.db.save(current, "concurrent")
        async for event in tool_chunks("ask_user", {"prompt": "请选择哪种存储？", "category": "decision", "options": ["本地", "云端"]}):
            yield event
    app.settings.stream = stream
    events = collect(app, p)
    current = app.db.get(p.id)
    assert current.target_draft == "Concurrent requirement"
    assert current.question is None
    assert app.unified.store.latest(p.id)["status"] == "stale"
    assert not events[-1]["changed"]


def test_answer_consumed_even_if_later_generation_fails(app):
    p = enable(app)
    rounds = 0
    async def stream(messages, schemas):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            async for event in tool_chunks("ask_user", {"prompt": "请选择哪种存储？", "category": "decision", "options": ["本地", "云端"]}):
                yield event
        else:
            raise ValueError("Fixture network failure")
    app.settings.stream = stream
    collect(app, p)
    current = app.db.get(p.id)
    assert current.question
    question_id = current.question.id
    record = app.unified.store.latest(p.id)
    assert record["base_revision"] == current.revision
    collect(app, p, "本地", question_id=question_id)
    current = app.db.get(p.id)
    assert current.question is None
    assert not current.milestones
    assert app.unified.store.latest(p.id)["base_revision"] == current.revision


def test_mechanism_revision_invalidates_existing_acceptance_identity(app):
    p = enable(app)
    provider(app)
    collect(app, p)
    canonical = app.db.get(p.id)
    old = canonical.behaviors[-1]
    canonical.plan_contract.sources.append(IntentSource(id="change", text="Use a different complete parser"))
    record = {"id": "change", "project": canonical.model_dump(), "created_at": now(), "base_revision": canonical.revision,
              "revision": canonical.revision, "status": "generating"}
    db = StagedDatabase(app.db, app.unified.store, record, "change")
    ctx = ToolContext(p.id, type("Facade", (), {"db": db})())
    trace_plan(ctx, TracePlan(bindings=[{**TRACE["bindings"][0], "mechanism": "Use another complete parser"}]))
    staged = db.get(p.id)
    assert staged.behaviors[-1].id != old.id
    assert staged.behaviors[-1].supersedes == old.id
    assert staged.behaviors[-1].statement == old.statement
    assert staged.plan_contract.bindings[0].behavior_revision_id == staged.behaviors[-1].id
    assert app.db.get(p.id).behaviors[-1].id == old.id


def test_requirement_replacement_preserves_exact_history(app):
    p = enable(app)
    provider(app)
    collect(app, p)
    canonical = app.db.get(p.id)
    from evograph.domain.plan_contracts import history_findings
    before = canonical.model_copy(deep=True)
    canonical.plan_contract.sources.append(IntentSource(id="change", text="Only check inline Markdown links now"))
    record = {"id": "change", "project": canonical.model_dump(), "created_at": now(), "base_revision": canonical.revision,
              "revision": canonical.revision, "status": "generating"}
    db = StagedDatabase(app.db, app.unified.store, record, "change")
    ctx = ToolContext(p.id, type("Facade", (), {"db": db})())
    trace_plan(ctx, TracePlan(retire_requirements=[{"id": "links", "quote": "Only check inline Markdown links now", "reason": "User changed goal"}],
        add_requirements=[{"id": "inline", "quote": "Only check inline Markdown links now", "kind": "outcome"}]))
    staged = db.get(p.id)
    assert staged.plan_contract.requirements[0].quote == INPUT
    assert not staged.plan_contract.requirements[0].active
    assert staged.plan_contract.requirements[0].retired_by.source_id == "change"
    assert not history_findings(before, staged)
    staged.plan_contract.requirements[0].quote = "Silently changed"
    assert history_findings(before, staged)[0]["code"] == "requirement_history_changed"


@pytest.mark.parametrize("action,params", [("design.update", {}), ("design.diagram", {}), ("plan.apply", {"expected_revision": 0}), ("agent.chat", {"content": "hello"})])
def test_legacy_planning_api_cannot_bypass_unified_gate(app, action, params):
    p = enable(app)
    result = asyncio.run(app.dispatch(action, {"project_id": p.id, **params}))
    assert not result["ok"]
    assert "统一候选规划" in result["error"]["message"]
    assert app.db.get(p.id) == p


def test_readonly_explanation_does_not_review_or_publish(app):
    p = enable(app)
    provider(app)
    collect(app, p)
    before = app.db.get(p.id)
    calls = 0
    async def stream(messages, schemas):
        nonlocal calls
        calls += 1
        assert schemas[0]["function"]["name"] != "submit_plan_review"
        yield {"type": "text", "text": "解释现有规划，没有修改"}
    app.settings.stream = stream
    events = collect(app, p, "Explain current plan without changes")
    assert app.db.get(p.id) == before
    assert calls == 1
    assert not events[-1]["changed"]
    assert events[-1]["summary"]["status"] == "completed"


def test_stale_discard_cannot_clear_newer_question(app):
    p = enable(app)
    rounds = 0
    async def stream(messages, schemas):
        nonlocal rounds
        rounds += 1
        async for event in tool_chunks("ask_user", {"prompt": f"请选择第{rounds}种存储？", "category": "decision", "options": ["本地", "云端"]}):
            yield event
    app.settings.stream = stream
    collect(app, p)
    a = app.unified.store.latest(p.id)
    q1 = app.db.get(p.id).question
    collect(app, p, "本地", question_id=q1.id)
    before = app.db.get(p.id)
    b = app.unified.store.latest(p.id)
    assert before.question and before.question.id != q1.id
    with pytest.raises(ConflictError, match="当前版本"):
        app.unified.discard(p.id, a["id"])
    assert app.db.get(p.id) == before
    assert app.unified.store.latest(p.id)["id"] == b["id"]
    app.unified.discard(p.id, b["id"])
    assert app.db.get(p.id).question is None
    assert app.unified.store.latest(p.id) is None


def test_review_inputs_and_external_handoffs_preserve_contract_sources(app):
    from evograph.application.acceptance import AcceptanceService
    from evograph.domain.models import AcceptanceRequest
    p = enable(app)
    provider(app)
    collect(app, p)
    record = app.unified.store.latest(p.id)
    packet = record["review_inputs"][0]
    assert packet["candidate_hash"] == record["reviews"][0]["candidate_hash"]
    assert packet["candidate"]["plan_contract"]["sources"][0]["text"] == INPUT
    saved = app.db.get(p.id)
    m = saved.milestones[0]
    request = AcceptanceRequest(milestone_id=m.id, baseline_id="fixture", fingerprint="fixture", behavior_revision_ids=m.behavior_revision_ids, architecture_revision=0)
    for prompt in [AcceptanceService._implementation_prompt(saved, m), AcceptanceService._acceptance_prompt(saved, m, request, {})]:
        assert INPUT in prompt
        assert TRACE["bindings"][0]["mechanism"] in prompt
        assert m.behavior_revision_ids[0] in prompt
        assert "requires_behavior_keys" in prompt


def test_optional_research_cannot_consume_synthesis_round(app):
    p=enable(app)
    calls=0
    linked=json.loads(json.dumps(NODE))
    linked['behaviors'][0].update({k:v for k,v in TRACE['bindings'][0].items() if k!='behavior_key'})
    async def stream(messages,schemas):
        nonlocal calls
        calls+=1
        names={s['function']['name'] for s in schemas}
        if names=={'submit_plan_review'}:
            packet=json.loads(messages[-1]['content'])
            name,args='submit_plan_review',review_for(packet)
        elif calls==1:
            assert 'inspect_repository' in names
            name,args='inspect_repository',{}
        else:
            assert names=={'submit_plan_delta','ask_user','validate_candidate'}
            name,args='submit_plan_delta',delta_fixture()
        async for e in tool_chunks(name,args):
            yield e
    app.settings.stream=stream
    events=collect(app,p)
    assert events[-1]['changed']
    assert calls==3
    assert app.unified.store.latest(p.id)['metrics']['dispatched_calls']==3


def test_continuation_can_anchor_unextracted_pending_source_without_repeating_it(app):
    p=enable(app)
    async def fail(messages,schemas):
        raise ValueError('fixture unavailable before extraction')
        yield
    app.settings.stream=fail
    collect(app,p)
    previous=app.unified.store.latest(p.id)
    old_source=previous['project']['plan_contract']['sources'][0]['id']
    linked=json.loads(json.dumps(NODE))
    linked['behaviors'][0].update({k:v for k,v in TRACE['bindings'][0].items() if k!='behavior_key'})
    calls=0
    async def stream(messages,schemas):
        nonlocal calls
        calls+=1
        names={s['function']['name'] for s in schemas}
        if names=={'submit_plan_review'}:
            packet=json.loads(messages[-1]['content'])
            name,args='submit_plan_review',review_for(packet)
        else:
            assert names=={'submit_plan_delta','ask_user','validate_candidate'}
            name,args='submit_plan_delta',delta_fixture([{**TRACE['add_requirements'][0],'source_id':old_source}])
        async for e in tool_chunks(name,args):
            yield e
    app.settings.stream=stream
    events=collect(app,p,'Continue the preserved request')
    assert events[-1]['changed']
    saved=app.db.get(p.id)
    assert saved.plan_contract.requirements[0].source_id==old_source
    assert saved.plan_contract.requirements[0].quote==INPUT
    assert calls==2


def test_committed_historical_source_cannot_be_reintroduced_as_new_requirement(app):
    p=enable(app)
    provider(app)
    collect(app,p)
    current=app.db.get(p.id)
    old_source=current.plan_contract.sources[0].id
    current.plan_contract.sources.append(IntentSource(id='new',text='Only explain current design'))
    record={'id':'new','project':current.model_dump(),'created_at':now(),'base_revision':current.revision,'revision':current.revision,'status':'generating','allowed_requirement_source_ids':['new']}
    db=StagedDatabase(app.db,app.unified.store,record,'new')
    ctx=ToolContext(p.id,type('Facade',(),{'db':db})())
    with pytest.raises(ValueError,match='尚未应用'):
        trace_plan(ctx,TracePlan(add_requirements=[{'id':'old-again','source_id':old_source,'quote':INPUT,'kind':'outcome'}]))
