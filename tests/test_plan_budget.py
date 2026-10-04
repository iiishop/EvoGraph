import asyncio
from types import SimpleNamespace

import pytest
from evograph.application.plan_budget import BudgetedSettings, encoded_size


def metrics(**limits):
    return {"provider_calls": 0, "tokens": 0, "usage_reported": False, "budget": limits}


def run(settings, messages=None, tools=None):
    async def collect():
        return [e async for e in settings.stream(messages or [{"role":"user","content":"hello"}], tools or [])]
    return asyncio.run(collect())


def fake(*events):
    async def stream(messages, tools):
        for event in events:
            yield event
    return SimpleNamespace(stream=stream, secrets=None)


def test_cumulative_usage_is_counted_once_and_raw_details_retained():
    def usage(n):
        return {"type":"usage", "tokens":n,"usage_counter":"openai_total", "usage_details":{"prompt_cache_hit_tokens":80}}
    m=metrics()
    result=run(BudgetedSettings(fake(usage(100),usage(100),usage(120)),m))
    assert m["tokens"]==120
    assert [e["tokens"] for e in result if e["type"] == "usage"]==[100,0,20]
    assert len(m["calls"][0]["usage_events"])==3
    assert m["calls"][0]["usage_events"][0]["usage_details"]["prompt_cache_hit_tokens"]==80


def test_separate_anthropic_input_output_counters():
    m=metrics()
    run(BudgetedSettings(fake({"type":"usage","tokens":100,"usage_counter":"anthropic_input"},
        {"type":"usage","tokens":20,"usage_counter":"anthropic_output"},
        {"type":"usage","tokens":30,"usage_counter":"anthropic_output"}),m))
    assert m["tokens"]==130


def test_unknown_usage_is_not_reported_as_zero_consumption():
    m=metrics()
    run(BudgetedSettings(fake({"type":"text","text":"ok"}),m))
    assert not m["usage_reported"]
    assert m["provider_calls"]==1
    assert m["calls"][0]["output_bytes"]>0


@pytest.mark.parametrize("limit,value", [("max_calls",0),("max_request_bytes",1),("max_total_input_bytes",1)])
def test_budget_failure_happens_before_provider_call(limit,value):
    m=metrics(**{limit:value})
    invoked=[]
    async def stream(messages, tools):
        invoked.append(True)
        yield {"type":"text","text":"wrong"}
    with pytest.raises(ValueError):
        run(BudgetedSettings(SimpleNamespace(stream=stream,secrets=None),m))
    assert not invoked
    assert m["provider_calls"]==0


def test_output_limit_closes_provider_and_retains_audit():
    m=metrics(max_output_bytes=20)
    with pytest.raises(ValueError,match="输出"):
        run(BudgetedSettings(fake({"type":"text","text":"x"*100}),m))
    assert m["calls"][0]["status"]=="interrupted"
    assert m["calls"][0]["elapsed_seconds"]>=0


def test_schema_titles_removed_but_validation_and_descriptions_retained():
    saved=[]
    m=metrics()
    schema={"type":"function","function":{"name":"test","parameters":{"title":"Verbose","type":"object","properties":{"field":{"type":"string","description":"required semantics","minLength":1}},"required":["field"]}}}
    run(BudgetedSettings(fake(),m,lambda **kw:saved.append(kw)),tools=[schema])
    request=saved[0]["request"]
    assert "title" not in request["tools"][0]["function"]["parameters"]
    assert request["tools"][0]["function"]["parameters"]["required"]==["field"]
    assert request["tools"][0]["function"]["parameters"]["properties"]["field"]["description"]=="required semantics"
    assert m["input_bytes"]==encoded_size(request)


def test_compaction_preserves_required_title_properties_and_literal_defaults():
    from evograph.application.plan_budget import compact_schema
    from evograph.application.plan_patch import PATCH_TOOL
    schema = compact_schema(PATCH_TOOL.schema())['function']['parameters']
    assert 'title' in schema['$defs']['PatchMilestone']['properties']
    assert 'title' in schema['$defs']['PatchMilestone']['required']
    assert 'title' in schema['$defs']['Diagram']['properties']
    assert compact_schema({'default':{'title':'literal data'}})=={'default':{'title':'literal data'}}


def test_cancellation_before_dispatch_is_terminal_and_not_a_provider_invocation():
    m = metrics()
    invoked = []
    async def stream(messages, tools):
        invoked.append(True)
        yield {"type": "text", "text": "never"}
    async def cancel():
        worker = BudgetedSettings(SimpleNamespace(stream=stream, secrets=None), m).stream([], [])
        assert (await anext(worker))["type"] == "request_started"
        await worker.aclose()
    asyncio.run(cancel())
    assert not invoked
    assert m["provider_calls"] == 1  # admitted attempt, not claimed dispatched
    assert m.get("dispatched_calls", 0) == 0
    assert m["calls"][0]["status"] == "cancelled_before_dispatch"
    assert m["calls"][0]["elapsed_seconds"] >= 0
