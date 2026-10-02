import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from evograph.application.projects import ProjectService
from evograph.domain.models import Project
from evograph.infrastructure.database import Database


def test_archived_list_is_discoverable_after_restart_and_has_only_recorded_metadata(
    app, repository
):
    one = app.projects.create("One", "Description", str(repository))
    two = app.projects.create("Two")
    active = app.projects.create("Active")
    app.projects.delete(one.id)
    app.projects.delete(two.id)
    reopened = ProjectService(Database(app.db.path))
    records = reopened.list_archived()
    assert {p["id"] for p in records} == {one.id, two.id}
    assert [p["id"] for p in reopened.list()] == [active.id]
    saved = next(p for p in records if p["id"] == one.id)
    assert saved["repository"] == str(repository)
    assert saved["description"] == "Description"
    assert saved["updated_at"] == app.db.get(one.id).updated_at
    assert saved["milestone_count"] == 0
    assert "deleted_at" not in saved
    assert "messages" not in saved
    assert (repository / "auth.py").read_text() == "def login(): return True\n"
    reopened.restore(one.id)
    assert [p["id"] for p in reopened.list_archived()] == [two.id]


def test_archived_listing_uses_shared_api_and_is_readonly(app):
    project = app.projects.create("Archived")
    app.projects.delete(project.id)
    lock = app.operation_lock("__global__")
    with lock:
        result = asyncio.run(app.dispatch("projects.list_archived", {}))
    assert result["ok"]
    assert [p["id"] for p in result["data"]] == [project.id]


def test_restore_retry_of_active_repository_project_is_idempotent(app, repository):
    project = app.projects.create("Recover", repository=str(repository))
    app.projects.delete(project.id)
    restored = app.projects.restore(project.id)
    before = app.db.events(project.id)
    retried = app.projects.restore(project.id)
    assert retried == restored
    assert app.db.events(project.id) == before
    assert sum(event["kind"] == "project_restored" for event in before) == 1
    assert len(app.projects.list()) == 1
    assert app.projects.list_archived() == []


def test_restore_preserves_normalized_repository_collision_and_can_retry_when_resolved(
    app, repository
):
    archived = app.db.create(Project(name="Archived", repository=str(repository / ".")))
    app.projects.delete(archived.id)
    # Model-level records can carry legacy non-normalized paths; Database.save
    # must remain the final authority even if the service's string check misses.
    (repository / "nested").mkdir()
    active = app.db.create(Project(name="Active", repository=str(repository / "nested" / "..")))
    before = app.db.get(archived.id)
    with pytest.raises(ValueError, match="已有活跃项目"):
        app.projects.restore(archived.id)
    assert app.db.get(archived.id) == before
    assert [p["id"] for p in app.projects.list_archived()] == [archived.id]
    assert not any(e["kind"] == "project_restored" for e in app.db.events(archived.id))
    app.projects.delete(active.id)
    assert app.projects.restore(archived.id).id == archived.id
    assert not app.db.get(archived.id).archived
    assert (repository / "auth.py").is_file()


def test_two_archived_projects_cannot_restore_the_same_repository_concurrently(app, repository):
    one = app.projects.create("One", repository=str(repository))
    app.projects.delete(one.id)
    two = app.projects.create("Two", repository=str(repository))
    app.projects.delete(two.id)
    barrier = threading.Barrier(2)

    def restore(project):
        barrier.wait()
        try:
            return app.projects.restore(project.id).id
        except ValueError as error:
            assert "已有活跃项目" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(restore, [one, two]))
    assert sum(outcome is not None for outcome in outcomes) == 1
    assert len(app.projects.list()) == len(app.projects.list_archived()) == 1
    assert (
        sum(
            event["kind"] == "project_restored"
            for project in [one, two]
            for event in app.db.events(project.id)
        )
        == 1
    )


def test_shared_api_restore_retries_after_a_lost_followup_read_without_a_second_event(
    app, repository
):
    project = app.projects.create("Recover", repository=str(repository))
    app.projects.delete(project.id)
    first = asyncio.run(app.dispatch("projects.restore", {"project_id": project.id}))
    assert first["ok"]
    # The client may not have been able to refresh after the confirmed write.
    second = asyncio.run(app.dispatch("projects.restore", {"project_id": project.id}))
    assert second["ok"]
    assert second["data"]["revision"] == first["data"]["revision"]
    assert sum(e["kind"] == "project_restored" for e in app.db.events(project.id)) == 1


def test_restore_waits_for_an_inflight_lifecycle_operation_and_is_retryable(app):
    project = app.projects.create("Recover")
    app.projects.delete(project.id)
    with app.operation_lock(project.id):
        blocked = asyncio.run(app.dispatch("projects.restore", {"project_id": project.id}))
    assert blocked["error"]["code"] == "BUSY"
    assert app.db.get(project.id).archived
    assert not any(e["kind"] == "project_restored" for e in app.db.events(project.id))
    retried = asyncio.run(app.dispatch("projects.restore", {"project_id": project.id}))
    assert retried["ok"]
    assert not app.db.get(project.id).archived


def test_recovery_api_reports_changed_or_already_active_without_changing_legacy_contract(app):
    project = app.projects.create("Original", "Before")
    app.projects.delete(project.id)
    first = asyncio.run(app.dispatch("projects.restore_archived", {"project_id": project.id}))
    assert first["ok"] and first["data"]["restored"] is True
    assert first["data"]["project"]["id"] == project.id
    assert first["data"]["project"]["acceptance"] == app.projects.list()[0]["acceptance"]
    current = app.projects.update(project.id, "Renamed after recovery", "New description", "")
    before_events = app.db.events(project.id)
    second = asyncio.run(app.dispatch("projects.restore_archived", {"project_id": project.id}))
    assert second["ok"] and second["data"]["restored"] is False
    assert second["data"]["project"]["id"] == project.id
    assert second["data"]["project"]["name"] == current.name
    assert second["data"]["project"]["description"] == current.description
    assert second["data"]["project"]["revision"] == current.revision
    assert app.db.events(project.id) == before_events
    legacy = asyncio.run(app.dispatch("projects.restore", {"project_id": project.id}))
    assert legacy["ok"] and legacy["data"]["id"] == project.id
    assert "restored" not in legacy["data"]
    assert "project" not in legacy["data"]
    assert app.db.events(project.id) == before_events
