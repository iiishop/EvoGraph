"""Product contracts, request-scoped instructions and failed-tool audit are distinct."""
import asyncio
import json
from copy import deepcopy

import pytest
from evograph.application.plan_contracts import TracePlan, trace_plan
from evograph.application.plan_patch import PATCH_TOOL, PlanPatch, propose_plan_patch
from evograph.application.tool_execution import ToolExecutor
from evograph.domain.models import Project
from evograph.domain.plan_contracts import (
    candidate_hash,
    contract_findings,
    history_findings,
    planning_fingerprint,
    review_subjects,
)
from test_plan_patch import INPUT, initial, node, stage


def test_process_instruction_is_not_a_product_requirement(app):
    ctx, db = stage(app, text=INPUT + ' Only plan. Do not research online.')
    initial(ctx, process_constraints=[
        {'id': 'plan', 'quote': 'Only plan.', 'rule': 'planning_only'},
        {'id': 'research', 'quote': 'Do not research online.', 'rule': 'no_external_research'},
    ])
    p = db.get(ctx.project_id)
    assert [r.id for r in p.plan_contract.requirements] == ['r']
    assert len(p.plan_contract.process_constraints) == 2
    assert not contract_findings(p)
    assert 'process:' + db.source_id + ':research' in review_subjects(p)
    assert set(p.plan_contract.bindings[0].requirement_ids) == {'r'}


def test_process_instruction_anchor_and_immutable_history(app):
    ctx, db = stage(app, text=INPUT + ' Only plan.')
    initial(ctx, process_constraints=[{'id': 'plan', 'quote': 'Only plan.', 'rule': 'planning_only'}])
    before = db.get(ctx.project_id)
    with pytest.raises(ValueError, match='不可覆盖'):
        trace_plan(ctx, TracePlan(process_constraints=[{'id': 'plan', 'quote': 'Only plan.', 'rule': 'other'}]))
    with pytest.raises(ValueError, match='精确原话'):
        trace_plan(ctx, TracePlan(process_constraints=[{'id': 'false', 'quote': 'not supplied', 'rule': 'other'}]))
    modified = before.model_copy(deep=True)
    modified.plan_contract.process_constraints.clear()
    assert history_findings(before, modified)[0]['code'] == 'process_history_changed'
    assert db.get(ctx.project_id) == before


def test_failed_search_attempt_cannot_be_hidden_from_process_check(app):
    ctx, db = stage(app, text=INPUT + ' Do not research online.')
    executor = ToolExecutor(ctx, {})
    result = asyncio.run(executor.invoke('web_search', '{"query":"unsupported"}'))
    assert not result['payload']['ok']
    before = db.get(ctx.project_id)
    with pytest.raises(ValueError, match='外部调查尝试'):
        initial(ctx, process_constraints=[{'id': 'research', 'quote': 'Do not research online.', 'rule': 'no_external_research'}])
    assert db.get(ctx.project_id) == before
    assert db.record['tool_attempts'][0]['status'] == 'failed'
    assert db.record['project']['plan_contract']['sources'][0]['activity'][0]['name'] == 'web_search'


def test_final_malformed_call_is_durable_without_next_model_request(app):
    ctx, db = stage(app)
    executor = ToolExecutor(ctx, {'propose_plan_patch': PATCH_TOOL})
    result = asyncio.run(executor.invoke('propose_plan_patch', '{"milestones":['))
    saved = db.store.get(db.record['id'])
    attempt = saved['tool_attempts'][0]
    assert attempt['raw_arguments'] == '{"milestones":['
    assert attempt['status'] == 'failed'
    assert attempt['payload'] == result['payload']
    assert 'Invalid JSON' in attempt['payload']['error']
    assert saved['project']['milestones'] == app.db.get(ctx.project_id).milestones == []


def test_compilation_audit_is_same_checkpoint_and_unchanged_on_failure(app):
    ctx, db = stage(app)
    audit = {'protocol_version': 'plan-delta/v1', 'ir': {'example': True}, 'compiled_hash': 'fixture'}
    args = PlanPatch(target=INPUT, add_requirements=[{'id': 'r', 'quote': INPUT, 'kind': 'outcome'}], milestones=[node()])
    propose_plan_patch(ctx, args, compiler_audit=audit)
    saved = db.store.get(db.record['id'])
    assert saved['compilations'] == [audit]
    assert saved['project']['milestones']
    before = deepcopy(saved)
    with pytest.raises(ValueError):
        propose_plan_patch(ctx, PlanPatch(remove_milestone_ids=['absent']), compiler_audit={'bad': True})
    assert db.store.get(db.record['id']) == before


def test_process_only_id_cannot_replace_product_trace(app):
    ctx, db = stage(app, text=INPUT + ' Only plan.')
    m = node()
    m['behaviors'][0]['requirement_ids'] = ['plan']
    with pytest.raises(ValueError, match='invalid_requirement_link'):
        initial(ctx, milestones=[m], process_constraints=[{'id': 'plan', 'quote': 'Only plan.', 'rule': 'planning_only'}])


@pytest.mark.parametrize('repair_tool', ['validate_candidate', 'submit_plan_delta'])
def test_audit_only_repair_does_not_retry_failed_semantic_review(app, repair_tool):
    from test_agent_stream import tool_chunks
    from test_unified_planning import collect, delta_fixture, enable, review_for

    before = enable(app)
    calls = []

    async def stream(messages, schemas):
        names = {schema['function']['name'] for schema in schemas}
        if names == {'submit_plan_review'}:
            calls.append('review')
            packet = json.loads(messages[-1]['content'])
            name = 'submit_plan_review'
            # If a no-op repair incorrectly requests another review, a different
            # model answer must not let the unchanged failed plan be published.
            verdict = 'unknown' if calls.count('review') == 1 else 'supported'
            arguments = review_for(packet, verdict)
        else:
            calls.append('generation')
            name, arguments = ('submit_plan_delta', delta_fixture()) if len(calls) == 1 else (repair_tool, {})
        async for event in tool_chunks(name, arguments):
            yield event

    app.settings.stream = stream
    events = collect(app, before)
    record = app.unified.store.latest(before.id)
    candidate = Project.model_validate(record['project'])
    reviewed = record['review_inputs'][0]

    assert calls == ['generation', 'review', 'generation']
    assert record['metrics']['provider_calls'] == 3
    assert len(record['reviews']) == len(record['review_inputs']) == 1
    assert record['status'] == 'needs_resolution'
    assert planning_fingerprint(candidate) == reviewed['planning_fingerprint']
    assert candidate_hash(candidate) != reviewed['candidate_hash']
    assert record['tool_attempts'][-1]['name'] == repair_tool
    assert record['tool_attempts'][-1]['status'] == 'succeeded'
    assert not events[-1]['changed']
    assert app.db.get(before.id) == before
