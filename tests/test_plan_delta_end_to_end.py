"""Full shallow protocol flows with deterministic responses, not AI design-quality tests."""
import asyncio
import json
from pathlib import Path

from evograph.domain.plan_contracts import active_behaviors
from test_agent_stream import tool_chunks
from test_unified_planning import collect, enable, review_for

FIXTURES = Path(__file__).parent / 'fixtures'
INPUT = (FIXTURES / 'converter-plan-input.txt').read_text()
DELTA = json.loads((FIXTURES / 'converter-plan-delta.json').read_text())


def scripted(app, delta, verdict='supported'):
    calls = []
    async def stream(messages, schemas):
        names = {t['function']['name'] for t in schemas}
        calls.append(names)
        if names == {'submit_plan_review'}:
            name, arguments = 'submit_plan_review', review_for(json.loads(messages[-1]['content']), verdict)
        else:
            name, arguments = 'submit_plan_delta', delta
        async for event in tool_chunks(name, arguments):
            yield event
    app.settings.stream = stream
    return calls


def test_complete_flat_fixture_auto_applies_in_two_calls_with_mock_judgment(app):
    p = enable(app)
    calls = scripted(app, DELTA)
    events = collect(app, p, INPUT)
    result = app.db.get(p.id)
    assert len(calls) == 2 and events[-1]['changed']
    assert len(result.milestones) == 2
    assert len(result.plan_contract.requirements) == 7
    assert len(result.plan_contract.process_constraints) == 3
    assert len(active_behaviors(result)) == 4
    assert result.milestone('CLI').dependencies == ['CORE']
    record = app.unified.store.latest(p.id)
    assert record['compilations'][0]['ir'] == DELTA
    assert record['tool_attempts'][0]['raw_arguments']
    assert len(record['review_inputs']) == 1


def test_one_slice_title_change_does_not_resubmit_other_contracts(app):
    p = enable(app)
    scripted(app, DELTA)
    collect(app, p, INPUT)
    before = app.db.get(p.id)
    small = {'slices': [{'id': 'CLI', 'title': '终端入口'}]}
    calls = scripted(app, small)
    collect(app, p, '仅把 CLI 的标题改为“终端入口”，其他保持不变。')
    after = app.db.get(p.id)
    assert len(calls) == 2
    assert after.behaviors == before.behaviors
    assert after.architectures == before.architectures
    assert after.plan_contract.requirements == before.plan_contract.requirements
    assert after.milestone('CORE') == before.milestone('CORE')
    assert after.milestone('CLI').title == '终端入口'
    assert app.unified.store.latest(p.id)['compilations'][0]['ir'] == small


def test_final_bad_delta_then_plain_reply_remains_failure_and_auditable(app):
    p = enable(app)
    count = 0
    async def stream(messages, schemas):
        nonlocal count
        count += 1
        if count == 1:
            async for e in tool_chunks('submit_plan_delta', {'architecture': {'milestones': []}}):
                yield e
        else:
            yield {'type': 'text', 'text': '无法形成有效规划'}
    app.settings.stream = stream
    collect(app, p, INPUT)
    record = app.unified.store.latest(p.id)
    assert record['status'] == 'failed'
    assert any(f['code'] == 'invalid_plan_delta' for f in record['report']['findings'])
    assert record['tool_attempts'][0]['status'] == 'failed'
    assert app.db.get(p.id) == p
    assert count == 2


def test_raw_json_error_is_not_repaired_by_application(app):
    p = enable(app)
    raw = json.dumps(DELTA, ensure_ascii=False)[:-1]
    async def stream(messages, schemas):
        yield {'type': 'tool_delta', 'index': 0, 'name': 'submit_plan_delta', 'arguments': raw}
    app.settings.stream = stream
    async def run():
        return [event async for event in app.agent.stream(p.id, INPUT)]
    asyncio.run(run())
    record = app.unified.store.latest(p.id)
    assert record['status'] == 'failed' and not record['project']['milestones']
    assert len(record['tool_attempts']) == 2
    assert all(attempt['raw_arguments'] == raw and attempt['status'] == 'failed'
               for attempt in record['tool_attempts'])
    assert app.db.get(p.id) == p
