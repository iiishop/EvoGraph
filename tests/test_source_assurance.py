import asyncio
import json

import pytest
from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.agent_tools.repository import ReadFile, read_repository_file
from evograph.application.research import public_url


def test_source_index_is_idempotent_and_not_acceptance(app, planned, repository):
    (repository / "ui").mkdir()
    (repository / "ui/view.ts").write_text('import {login} from "../auth";')
    (repository / "auth.ts").write_text("export const login = () => true;")
    p = app.execution.refresh(planned.id)
    assert p.source_milestones and all(m.origin == "source" for m in p.source_milestones)
    assert p.source_diagram.edges
    assert len(p.milestones) == 1
    target_ids = p.targets[-1].required_behavior_ids
    ids = [m.id for m in p.source_milestones]
    again = app.execution.refresh(p.id)
    assert again.revision == p.revision
    assert [m.id for m in again.source_milestones] == ids
    assert again.targets[-1].required_behavior_ids == target_ids
    (repository / "ui/view.ts").unlink()
    updated = app.execution.refresh(p.id)
    assert len(updated.source_milestones) == len(ids) - 1
    assert updated.targets[-1].required_behavior_ids == target_ids


def test_investigation_requires_actual_reads_and_invalidates(app, planned, repository):
    ctx = ToolContext(planned.id, app)
    kwargs = dict(
        milestone_id="M01",
        obligation_id="scope",
        paths=["auth.py"],
        conclusion="已读取 auth.py，当前 login 返回固定值，尚未支持实际身份验证。",
    )
    with pytest.raises(ValueError, match="实际读取"):
        app.assurance.investigate(ctx, **kwargs)
    read_repository_file(ctx, ReadFile(path="auth.py"))
    app.assurance.investigate(ctx, **kwargs)
    obligation = app.db.get(planned.id).milestone("M01").obligations[0]
    assert obligation.investigator == "agent" and obligation.resolved
    (repository / "auth.py").write_text("def login(): return False\n")
    with pytest.raises(ValueError, match="发生变化"):
        app.assurance.investigate(ctx, **kwargs)
    p = app.execution.refresh(planned.id)
    assert not p.milestone("M01").obligations[0].resolved


def test_search_settings_credentials_and_results(app, planned, monkeypatch):
    from evograph.web_search import providers

    app.research.configure("tavily", {}, "secret123", vision_enabled=True)
    saved = app.research.settings()
    assert "secret123" not in json.dumps(saved)
    assert saved["saved"]["has_key"]
    monkeypatch.setattr(
        providers()["tavily"],
        "search",
        lambda *_: [
            {
                "title": "Official documentation",
                "url": "https://example.com/docs",
                "excerpt": "untrusted data",
            },
            {"title": "Private", "url": "http://localhost/secret", "excerpt": "private"},
        ],
    )
    result = app.research.run(planned.id, "public technical query")
    assert len(result["sources"]) == 1
    assert app.db.get(planned.id).research[0].url == "https://example.com/docs"
    app.research.configure("searxng", {"base_url": "http://127.0.0.1:8080"})
    assert not app.research.settings()["saved"]["has_key"]
    with pytest.raises(ValueError, match="不支持正文"):
        app.research.run(planned.id, "https://example.com", extract=True)
    with pytest.raises(ValueError):
        public_url("http://127.0.0.1/private")


def test_planner_tools_cannot_execute_acceptance():
    registry = tools()
    assert {"web_search", "web_extract", "resolve_investigation", "save_uml"} <= registry.keys()
    assert not {"run_light_check", "discover_checks", "record_visual_review"} & registry.keys()


def test_verification_question_preserves_permission(app, planned):
    from test_agent_stream import tool_chunks

    calls = 0

    async def stream(messages, schemas):
        nonlocal calls
        calls += 1
        if calls == 1:
            async for event in tool_chunks(
                "ask_user",
                {
                    "prompt": "需要选择哪种检查范围？",
                    "category": "decision",
                    "options": ["测试范围", "验收范围"],
                },
            ):
                yield event
        else:
            yield {"type": "text", "text": "done"}

    app.settings.stream = stream

    async def run():
        return [e async for e in app.agent.stream(planned.id, "test", verification_milestone="M01")]

    asyncio.run(run())
    assert app.db.get(planned.id).question.verification_milestone == "M01"


def test_questions_have_admission_gate_and_separate_answers(app, planned):
    from evograph.agent_tools.questions import AskUser, ask_user

    ctx = ToolContext(planned.id, app)
    with pytest.raises(ValueError):
        ask_user(
            ctx,
            AskUser(
                prompt="请告诉我邮箱还是用户名？", category="decision", options=["邮箱", "用户名"]
            ),
        )
    with pytest.raises(ValueError, match="选项"):
        ask_user(
            ctx,
            AskUser(
                prompt="使用哪种登录方式？选项：邮箱、用户名",
                category="decision",
                options=["邮箱", "用户名"],
            ),
        )
    result = ask_user(
        ctx,
        AskUser(
            prompt="登录方式采用哪种身份标识？",
            category="decision",
            options=["邮箱", "用户名"],
            context="这会影响认证接口和行为契约。",
        ),
    )
    question = app.db.get(planned.id).question
    assert result["question"]["prompt"] == "登录方式采用哪种身份标识？"
    assert question.options == ["邮箱", "用户名"]
    assert app.db.messages(planned.id)[-1]["content"] == "登录方式采用哪种身份标识？"
