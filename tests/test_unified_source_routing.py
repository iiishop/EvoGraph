import asyncio

from test_agent_stream import tool_chunks
from test_baseline_milestones import reconstruction


def test_enabled_source_analysis_retains_dedicated_writer_without_planning_mutators(app, planned):
    app.unified.enable(planned.id, True)
    before = app.db.get(planned.id)
    calls = 0

    async def stream(messages, schemas):
        nonlocal calls
        calls += 1
        names = {s['function']['name'] for s in schemas}
        assert 'reconstruct_baseline_milestones' in names
        assert not names & {'create_milestone','update_milestone','set_target','update_architecture','submit_plan_delta'}
        if calls == 1:
            name, args = 'read_repository_file', {'path':'auth.py'}
        elif calls == 2:
            name, args = 'reconstruct_baseline_milestones', reconstruction(before).model_dump()
        else:
            yield {'type':'text','text':'Source analysis finished'}
            return
        async for event in tool_chunks(name,args):
            yield event
    app.settings.stream = stream
    async def run():
        return [event async for event in app.agent.stream(planned.id,'Reconstruct observed source capabilities',source_analysis=True)]
    events = asyncio.run(run())
    assert not [e for e in events if e['type'] in {'error','tool_failed'}]
    after = app.db.get(planned.id)
    assert after.source_analysis_baseline_id == after.baseline.id
    assert after.source_milestones[0].id == 'SRC_login'
    assert after.milestones == before.milestones
    assert after.behaviors == before.behaviors
    assert after.targets == before.targets
    assert after.evidence == before.evidence
    assert app.unified.store.latest(planned.id) is None


def test_source_question_resume_uses_persisted_operation_identity(app, planned):
    app.unified.enable(planned.id, True)
    calls=0
    async def stream(messages, schemas):
        nonlocal calls
        calls+=1
        names={s['function']['name'] for s in schemas}
        assert 'reconstruct_baseline_milestones' in names
        assert 'submit_plan_delta' not in names
        if calls==1:
            async for e in tool_chunks('ask_user',{'prompt':'Which source behavior matters?','category':'missing_design_input'}):
                yield e
        else:
            yield {'type':'text','text':'No further source changes'}
    app.settings.stream=stream
    async def run():
        first=[e async for e in app.agent.stream(planned.id,'Inspect sources',source_analysis=True)]
        question=app.db.get(planned.id).question
        assert question and first[-1]['summary']['status']=='waiting'
        assert app.db.is_source_analysis_question(planned.id,question.id)
        second=[e async for e in app.agent.stream(planned.id,'Login',question_id=question.id)]
        return second
    events=asyncio.run(run())
    assert not [e for e in events if e['type']=='error']
    assert app.db.get(planned.id).question is None
    assert app.unified.store.latest(planned.id) is None


def test_consumed_source_answer_failure_exposes_retry_identity_without_sticking_to_new_turns(app, planned):
    app.unified.enable(planned.id, True)
    before = app.db.get(planned.id)
    phase = "question"
    retry_calls = 0

    async def stream(messages, schemas):
        nonlocal retry_calls
        names = {schema["function"]["name"] for schema in schemas}
        if phase == "ordinary":
            assert "submit_plan_delta" in names
            assert "reconstruct_baseline_milestones" not in names
            yield {"type": "text", "text": "Ordinary planning explanation"}
            return
        assert "reconstruct_baseline_milestones" in names
        assert "submit_plan_delta" not in names
        if phase == "question":
            name, args = "ask_user", {
                "prompt": "Which source behavior matters?", "category": "missing_design_input",
            }
        elif phase == "answer":
            raise RuntimeError("Provider failed after consuming the source question")
        else:
            retry_calls += 1
            if retry_calls == 1:
                name, args = "read_repository_file", {"path": "auth.py"}
            elif retry_calls == 2:
                name, args = "reconstruct_baseline_milestones", reconstruction(before).model_dump()
            else:
                yield {"type": "text", "text": "Source reconstruction completed"}
                return
        async for event in tool_chunks(name, args):
            yield event

    app.settings.stream = stream

    async def collect(content, **kwargs):
        return [event async for event in app.agent.stream(planned.id, content, **kwargs)]

    asyncio.run(collect("Inspect sources", source_analysis=True))
    question = app.db.get(planned.id).question
    assert question
    phase = "answer"
    failed = asyncio.run(collect("Login", question_id=question.id))
    assert failed[-1]["summary"]["status"] == "failed"
    assert app.db.get(planned.id).question is None
    admission = next(event for event in failed if event["type"] == "started")
    assert admission["source_analysis"] is True
    phase = "retry"
    resumed = asyncio.run(collect("Continue the original source answer: Login", source_analysis=admission["source_analysis"]))
    assert resumed[-1]["summary"]["status"] == "completed"
    assert not [event for event in resumed if event["type"] in {"error", "tool_failed", "candidate_changed"}]
    after = app.db.get(planned.id)
    assert after.source_milestones[0].id == "SRC_login"
    assert after.milestones == before.milestones
    assert after.targets == before.targets
    assert app.unified.store.latest(planned.id) is None
    phase = "ordinary"
    ordinary = asyncio.run(collect("Explain the future plan"))
    assert any(event["type"] == "candidate_changed" for event in ordinary)


def test_source_only_request_never_finalizes_pending_target_or_planned_edges(app, planned):
    from evograph.domain.models import ProposedMilestone
    app.unified.enable(planned.id,True)
    app.graph.upsert(planned.id,ProposedMilestone(id='M02',title='B',intent='B contract',scope=['B'],dependencies=['M01'],dependency_reasons={'M01':'Needs A'},behaviors=[{'key':'b','statement':'B'}]),True)
    app.graph.upsert(planned.id,ProposedMilestone(id='M03',title='C',intent='C contract',scope=['C'],dependencies=['M01','M02'],dependency_reasons={'M01':'Needs A','M02':'Needs B'},behaviors=[{'key':'c','statement':'C'}]),True)
    app.graph.target(planned.id,'Uncommitted goal draft')
    before=app.db.get(planned.id)
    calls=0
    async def stream(messages,schemas):
        nonlocal calls
        calls+=1
        if calls==1:
            name,args='read_repository_file',{'path':'auth.py'}
        elif calls==2:
            name,args='reconstruct_baseline_milestones',reconstruction(before).model_dump()
        else:
            yield {'type':'text','text':'Done'}
            return
        async for e in tool_chunks(name,args):
            yield e
    app.settings.stream=stream
    async def run():
        return [e async for e in app.agent.stream(planned.id,'Only reconstruct source',source_analysis=True)]
    asyncio.run(run())
    after=app.db.get(planned.id)
    assert after.target_draft=='Uncommitted goal draft'
    assert after.targets==before.targets
    assert after.milestones==before.milestones
    assert after.source_milestones


def test_enabled_verification_investigation_does_not_publish_target_draft(app,planned):
    app.unified.enable(planned.id,True)
    app.graph.target(planned.id,'Pending goal must remain uncommitted')
    before=app.db.get(planned.id)
    calls=0
    async def stream(messages,schemas):
        nonlocal calls
        calls+=1
        names={s['function']['name'] for s in schemas}
        assert 'resolve_investigation' in names
        assert 'update_milestone' not in names
        if calls==1:
            name,args='read_repository_file',{'path':'auth.py'}
        elif calls==2:
            name,args='resolve_investigation',{'milestone_id':'M01','obligation_id':'scope','paths':['auth.py'],'conclusion':'Observed direct login placeholder; no authentication implementation is established'}
        else:
            yield {'type':'text','text':'Investigation recorded'}
            return
        async for e in tool_chunks(name,args):
            yield e
    app.settings.stream=stream
    async def run():return [e async for e in app.agent.stream(planned.id,'Inspect acceptance prerequisites',verification_milestone='M01')]
    events=asyncio.run(run())
    assert not [e for e in events if e['type'] in {'tool_failed','error'}]
    after=app.db.get(planned.id)
    assert after.target_draft==before.target_draft
    assert after.targets==before.targets
    assert after.behaviors==before.behaviors
