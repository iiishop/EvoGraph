"""Atomic creation outcomes are additive to the legacy aggregate contract."""

import asyncio
import multiprocessing
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import MemorySecrets
from evograph.application.api import Application
from evograph.domain.models import Project
from evograph.transport.desktop import DesktopBridge
from evograph.transport.http import create_app
from fastapi.testclient import TestClient


def command(app, action, params, transport="dispatch"):
    if transport == "desktop":
        return DesktopBridge(app).command(action, params)
    if transport == "http":
        with TestClient(create_app(app)) as client:
            response = client.post("/api/command", json={"action": action, "params": params})
            assert response.status_code == 200
            return response.json()
    return asyncio.run(app.dispatch(action, params))


def test_legacy_database_and_service_still_return_project_aggregates(app):
    direct = Project(name="Direct", creation_key="direct")
    assert app.db.create(direct) == direct
    assert isinstance(app.db.create(direct), Project)
    saved = app.projects.create("  Service  ", "Original", request_id="service")
    replay = app.projects.create("Ignored", "Not applied", request_id="service")
    assert isinstance(saved, Project) and isinstance(replay, Project)
    assert replay == saved == app.db.get(saved.id)
    assert saved.name == "Service"
    assert "outcome" not in saved.model_dump()


@pytest.mark.parametrize("transport", ["dispatch", "http", "desktop"])
def test_legacy_command_response_remains_the_unwrapped_aggregate(app, transport):
    params = {"name": "Legacy", "description": "Original", "request_id": "legacy"}
    created = command(app, "projects.create", params, transport)
    assert created["ok"]
    saved = app.db.get(created["data"]["id"])
    assert created == {"ok": True, "data": saved.model_dump()}
    assert command(app, "projects.create", {**params, "name": "Ignored"}, transport) == created
    assert not {"outcome", "project", "messages", "events", "acceptance"} & created["data"].keys()


@pytest.mark.parametrize("transport", ["dispatch", "http", "desktop"])
def test_additive_command_returns_atomic_outcomes_and_original_metadata(app, repository, transport):
    params = {
        "name": "Original",
        "description": "Keep this description",
        "repository": str(repository),
        "request_id": "original-request",
    }
    created = command(app, "projects.create_with_outcome", params, transport)
    assert created["ok"] and created["data"]["outcome"] == "created"
    saved = app.db.get(created["data"]["project"]["id"])
    assert created["data"]["project"] == saved.model_dump()
    assert not saved.archived and saved.created_at
    assert not {"messages", "events", "acceptance", "readiness"} & created["data"]["project"].keys()

    replay = command(
        app, "projects.create_with_outcome", {**params, "name": "Unapplied retry"}, transport
    )
    assert replay == {
        "ok": True,
        "data": {"outcome": "reused_request", "project": saved.model_dump()},
    }
    collision = command(
        app,
        "projects.create_with_outcome",
        {**params, "name": "Unapplied collision", "request_id": "different-request"},
        transport,
    )
    assert collision == {
        "ok": True,
        "data": {"outcome": "existing_repository", "project": saved.model_dump()},
    }
    assert app.db.list_projects() == [saved]
    assert app.db.events(saved.id) == []


def test_request_identity_precedes_an_earlier_repository_collision(app, repository):
    owner = app.projects.create("Earlier repository owner", repository=str(repository))
    original = app.projects.create("Repository-free original", "Saved", request_id="uncertain")
    result = app.projects.create_with_outcome(
        "Edited retry", "Unapplied", str(repository), "uncertain"
    )
    assert result.outcome == "reused_request"
    assert result.project == original
    assert app.db.get(owner.id) == owner
    assert len(app.db.list_projects()) == 2


def test_archived_request_identity_is_stable_and_never_resurrected(app, repository):
    original = app.projects.create("Archived", repository=str(repository), request_id="past")
    app.projects.delete(original.id)
    archived = app.db.get(original.id)
    owner = app.projects.create("Current owner", repository=str(repository), request_id="current")
    for _ in range(2):
        result = app.projects.create_with_outcome(
            "Changed retry", "Ignored", str(repository), "past"
        )
        assert result.outcome == "reused_request" and result.project == archived
        assert result.project.archived
        assert app.projects.create("Legacy retry", request_id="past") == archived
    assert app.db.list_projects(include_archived=True) == [archived, owner]
    assert app.db.list_projects() == [owner]
    assert [event["kind"] for event in app.db.events(archived.id)] == ["project_deleted"]


def test_direct_identity_is_stable_even_for_archived_records(app):
    original = app.db.create(Project(name="Original"))
    app.projects.delete(original.id)
    archived = app.db.get(original.id)
    result = app.db.create_with_outcome(Project(id=original.id, name="Not a restore"))
    assert result.outcome == "reused_request" and result.project == archived
    assert app.db.list_projects(include_archived=True) == [archived]


@pytest.mark.parametrize("archived", [False, True])
def test_request_recovery_precedes_repository_validation_after_directory_disappears(
    app, repository, archived
):
    params = {
        "name": "Original",
        "description": "Frozen payload",
        "repository": str(repository),
        "request_id": "lost-response",
    }
    original = app.projects.create(**params)
    if archived:
        app.projects.delete(original.id)
    saved = app.db.get(original.id)
    repository.rename(repository.with_name("moved-repository"))
    recovered = command(app, "projects.create_with_outcome", params)
    assert recovered == {
        "ok": True,
        "data": {"outcome": "reused_request", "project": saved.model_dump()},
    }
    assert app.projects.create(**params) == saved
    rejected = command(app, "projects.create_with_outcome", {**params, "request_id": "new-request"})
    assert not rejected["ok"] and "仓库目录不存在" in rejected["error"]["message"]
    assert app.db.list_projects(include_archived=True) == [saved]


def test_direct_database_create_retains_its_repository_validation_contract(app, tmp_path):
    project = Project(name="Direct caller", repository=str(tmp_path / "not-yet-present"))
    assert app.db.create(project) == project


def test_request_identity_precedes_direct_id_and_demo_identity(app):
    earlier = app.db.create(Project(name="Earlier demo", is_demo=True))
    original = app.db.create(Project(name="Original request", creation_key="request"))
    result = app.db.create_with_outcome(
        Project(id=earlier.id, name="Retry", creation_key="request", is_demo=True)
    )
    assert result.outcome == "reused_request" and result.project == original
    assert app.db.create(Project(name="Another demo", is_demo=True)) == earlier


def test_database_normalizes_repository_aliases_inside_the_transaction(app, repository, tmp_path):
    alias = tmp_path / "repository-alias"
    alias.symlink_to(repository, target_is_directory=True)
    original = app.db.create(Project(name="Original", repository=str(repository)))
    for path in (str(alias), f"{repository}/../{repository.name}"):
        result = app.db.create_with_outcome(Project(name="Collision", repository=path))
        assert result.outcome == "existing_repository" and result.project == original
    assert app.db.list_projects() == [original]


def test_empty_keys_and_empty_repository_do_not_deduplicate_unrelated_projects(app):
    first = app.projects.create_with_outcome("Same name")
    second = app.projects.create_with_outcome("Same name")
    assert first.outcome == second.outcome == "created"
    assert first.project.id != second.project.id


@pytest.mark.parametrize("outcome", ["created", "reused_request", "existing_repository"])
def test_confirmed_response_needs_only_the_committing_transaction(
    app, repository, monkeypatch, outcome
):
    params = {"name": "Requested", "repository": str(repository), "request_id": "request"}
    original = None
    if outcome != "created":
        original = app.projects.create(
            "Original",
            "Kept",
            str(repository),
            "request" if outcome == "reused_request" else "other",
        )
    connect = app.db.connect
    committed = False
    statements = []

    @contextmanager
    def only_transaction():
        nonlocal committed
        assert not committed, "Creation response attempted a post-commit database read"
        with connect() as connection:
            connection.set_trace_callback(statements.append)
            yield connection
        committed = True

    def forbidden_read(*args, **kwargs):
        raise AssertionError("Creation response must not depend on view/list/settings reads")

    with monkeypatch.context() as patch:
        patch.setattr(app.db, "connect", only_transaction)
        for name in ("get", "list_projects", "messages", "events", "setting"):
            patch.setattr(app.db, name, forbidden_read)
        response = command(app, "projects.create_with_outcome", params)
    assert committed and response["ok"]
    assert response["data"]["outcome"] == outcome
    saved = app.db.get(response["data"]["project"]["id"])
    assert response["data"]["project"] == saved.model_dump()
    if original:
        assert saved == original
    assert statements.index("BEGIN IMMEDIATE") < next(
        index for index, statement in enumerate(statements) if statement.startswith("SELECT")
    )
    assert statements[-1] == "COMMIT"


def test_failed_transaction_never_returns_a_confirmed_creation(app, monkeypatch):
    connect = app.db.connect

    @contextmanager
    def fail_before_commit():
        with connect() as connection:
            yield connection
            raise OSError("Synthetic commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(app.db, "connect", fail_before_commit)
        response = command(app, "projects.create_with_outcome", {"name": "Not saved"})
    assert not response["ok"]
    assert app.db.list_projects(include_archived=True) == []


def _process_create(data_dir, params, barrier, results):
    """Separate command registries and SQLite connections cannot share UI locks."""
    try:
        application = Application(Path(data_dir), MemorySecrets())
        barrier.wait(timeout=20)
        results.put(command(application, "projects.create_with_outcome", params))
    except Exception as error:
        results.put({"process_error": repr(error)})


@pytest.mark.parametrize("identity", ["request", "repository"])
def test_process_backed_concurrent_commands_choose_one_atomic_winner(app, repository, identity):
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(4), context.Queue()
    processes = []
    for index in range(4):
        params = {
            "name": f"Contender {index}",
            "description": f"Description {index}",
            "request_id": "shared" if identity == "request" else f"request-{index}",
            "repository": "" if identity == "request" else f"{repository}/../{repository.name}",
        }
        processes.append(
            context.Process(
                target=_process_create,
                args=(str(app.db.path.parent), params, barrier, results),
            )
        )
    try:
        for process in processes:
            process.start()
        responses = [results.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()
        results.join_thread()
    assert all(response.get("ok") for response in responses), responses
    outcomes = [response["data"]["outcome"] for response in responses]
    assert outcomes.count("created") == 1
    assert outcomes.count("reused_request" if identity == "request" else "existing_repository") == 3
    saved = app.db.list_projects(include_archived=True)
    assert len(saved) == 1
    assert all(response["data"]["project"] == saved[0].model_dump() for response in responses)
