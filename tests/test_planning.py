import asyncio

from conftest import apply_proposal, proposal


def test_chat_propose_review_apply_and_persist(app, repository):
    p = app.projects.create("Real project", repository=str(repository))
    captured = []

    async def complete(messages):
        if "[INVESTIGATION]" in messages[0]["content"]:
            return '{"paths":["auth.py"]}', 2
        captured.extend(messages)
        return proposal().model_dump_json(), 450

    app.settings.complete = complete
    result = asyncio.run(
        app.dispatch("agent.chat", {"project_id": p.id, "content": "Plan login", "propose": True})
    )
    assert result["ok"]
    p = app.db.get(p.id)
    assert p.proposal and not p.milestones
    assert p.metrics["model_tokens"] == 454
    assert "auth.py" in captured[1]["content"]
    result = asyncio.run(
        app.dispatch("plan.apply", {"project_id": p.id, "expected_revision": p.revision})
    )
    assert result["ok"]
    assert app.db.get(p.id).milestones[0].id == "M01"
    assert len(app.db.messages(p.id)) == 2


def test_investigation_cannot_read_outside_repo_or_secrets(app, repository):
    from evograph.application.investigation import InvestigationService

    (repository / ".env").write_text("SECRET_SENTINEL=hidden")
    (repository.parent / "outside.txt").write_text("OUTSIDE_SENTINEL")

    async def complete(messages):
        return '{"paths":["../outside.txt",".env","auth.py"]}', 2

    app.settings.complete = complete
    result = asyncio.run(InvestigationService(app.settings).gather(str(repository), "Plan auth"))
    assert result.status == "NO_PROGRESS"
    assert [item["path"] for item in result.inspected] == ["auth.py"]
    assert "SECRET_SENTINEL" not in result.context and "OUTSIDE_SENTINEL" not in result.context


def test_malformed_model_output_cannot_mutate_plan(app, planned):
    async def complete(messages):
        return '{"target":"incomplete"}', 100

    app.settings.complete = complete
    before = app.db.get(planned.id)
    result = asyncio.run(
        app.dispatch(
            "agent.chat", {"project_id": planned.id, "content": "Plan changes", "propose": True}
        )
    )
    assert not result["ok"]
    after = app.db.get(planned.id)
    assert after.milestones == before.milestones
    assert after.revision == before.revision


def test_keep_existing_milestone_preserves_behavior_and_evidence(app, planned):
    before = app.db.get(planned.id)
    after = apply_proposal(app, before, proposal())
    assert after.behaviors == before.behaviors
    assert len(after.targets) == 1
    assert len(after.plans) == 2


def test_settings_test_runs_selected_adapter(app, monkeypatch):
    from evograph.providers import adapters

    captured = {}

    async def complete(config, secret, messages):
        captured.update(config=config, secret=secret)
        return "OK", 2

    monkeypatch.setattr(adapters()["openai_compatible"], "complete", complete)
    app.settings.save(
        "openai_compatible",
        {"base_url": "http://127.0.0.1:9999/v1", "model": "fixture"},
        "test-key",
    )
    result = asyncio.run(app.settings.test())
    assert result["ok"] and captured["secret"] == "test-key"
