"""Receipts distinguish deterministic checks, model opinions and this turn's non-execution."""
import json

from test_agent_stream import tool_chunks
from test_unified_planning import collect, delta_fixture, enable, provider


def test_applied_turn_has_hash_bound_receipt_but_no_new_execution_claim(app):
    p = enable(app)
    provider(app)
    collect(app, p)
    record = app.unified.store.latest(p.id)
    receipt = record['validation_receipt']
    assert receipt['candidate_hash'] == record['candidate_hash']
    assert receipt['structural'] == {'status': 'clear', 'finding_count': 0}
    assert receipt['model']['kind'] == 'model_opinion'
    assert receipt['model']['status'] == 'no_issue_found'
    assert receipt['implementation'] == {'scope': 'current_planning_turn', 'status': 'not_run', 'existing_record_count': 0}
    assert record['review_inputs'][0]['available_execution_evidence'] == []
    assert record['applied_revision'] == app.db.get(p.id).revision


def test_unknown_opinion_does_not_become_verified_or_reused_after_repair_audit(app):
    p = enable(app)
    provider(app, verdict='unknown')
    collect(app, p)
    record = app.unified.store.latest(p.id)
    assert record['status'] == 'needs_resolution'
    assert record['validation_receipt']['model']['status'] == 'issues'
    assert record['validation_receipt']['implementation']['status'] == 'not_run'
    assert app.db.get(p.id) == p


def test_unavailable_model_keeps_structural_check_and_execution_unrun_separate(app):
    p = enable(app)
    provider(app, bad_review=True)
    collect(app, p)
    receipt = app.unified.store.latest(p.id)['validation_receipt']
    assert receipt['structural']['status'] == 'clear'
    assert receipt['model']['status'] == 'unavailable'
    assert receipt['implementation']['status'] == 'not_run'


def test_noop_does_not_invent_a_structural_or_model_check(app):
    p = enable(app)
    async def stream(messages, schemas):
        yield {'type': 'text', 'text': '这里只解释当前空计划'}
    app.settings.stream = stream
    collect(app, p, '只解释，不修改规划')
    receipt = app.unified.store.latest(p.id)['validation_receipt']
    assert receipt['structural']['status'] == receipt['model']['status'] == 'not_run'
    assert receipt['structural']['finding_count'] is None
    assert receipt['implementation']['status'] == 'not_run'


def test_invalid_last_delta_cannot_reuse_prior_receipt(app):
    p = enable(app)
    provider(app)
    collect(app, p)
    before = app.db.get(p.id)
    async def stream(messages, schemas):
        async for event in tool_chunks('submit_plan_delta', {'nonsense': []}):
            yield event
    app.settings.stream = stream
    collect(app, p, '改变当前计划但这是假数据错误测试')
    record = app.unified.store.latest(p.id)
    assert record['validation_receipt']['candidate_hash'] == record['candidate_hash']
    assert record['validation_receipt']['structural']['status'] == 'not_run'
    assert record['validation_receipt']['model']['status'] == 'not_run'
    assert app.db.get(p.id) == before


def test_fake_execution_reference_is_unavailable_not_automatic_apply(app):
    from test_unified_planning import review_for
    p = enable(app)
    async def stream(messages, schemas):
        if schemas[0]['function']['name'] == 'submit_plan_review':
            packet = json.loads(messages[-1]['content'])
            assert packet['available_execution_evidence'] == []
            answer = review_for(packet)
            answer['checks'][0].update(basis='existing_execution_record', execution_evidence_ids=['invented'])
            name, args = 'submit_plan_review', answer
        else:
            name, args = 'submit_plan_delta', delta_fixture()
        async for event in tool_chunks(name, args):
            yield event
    app.settings.stream = stream
    collect(app, p)
    record = app.unified.store.latest(p.id)
    assert record['validation_receipt']['model']['status'] == 'unavailable'
    assert record['status'] == 'needs_resolution'
    assert app.db.get(p.id) == p
