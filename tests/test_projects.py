from concurrent.futures import ThreadPoolExecutor


def test_baseline_refresh_never_creates_another_project(app, repository):
    p = app.projects.create("Project", repository=str(repository))
    for _ in range(3):
        app.execution.refresh(p.id)
    assert [item["id"] for item in app.projects.list()] == [p.id]
    assert len(app.db.get(p.id).baselines) == 1


def test_create_is_idempotent_under_concurrent_submissions(app, repository):
    with ThreadPoolExecutor(max_workers=4) as executor:
        projects = list(
            executor.map(
                lambda _: app.projects.create("Project", repository=str(repository)), range(4)
            )
        )
    assert len({p.id for p in projects}) == 1
    assert len(app.projects.list()) == 1


def test_creation_key_covers_projects_without_a_repository(app):
    first = app.projects.create("Untitled", request_id="same-request")
    second = app.projects.create("Untitled", request_id="same-request")
    assert first.id == second.id


def test_delete_and_restore_do_not_touch_repository(app, planned, repository):
    app.projects.delete(planned.id)
    assert not app.projects.list()
    assert (repository / "auth.py").is_file()
    assert app.db.get(planned.id).behaviors
    app.projects.restore(planned.id)
    assert app.projects.list()[0]["id"] == planned.id


def test_empty_workspace_does_not_recreate_deleted_demo(app):
    app.bootstrap()
    demo = app.projects.list()[0]
    app.projects.delete(demo["id"])
    app.bootstrap()
    assert app.projects.list() == []


def test_two_different_repositories_can_have_same_display_name(app, tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    a = app.projects.create("Same title", repository=str(one))
    b = app.projects.create("Same title", repository=str(two))
    assert a.id != b.id


def test_update_cannot_bind_another_active_projects_repository(app, repository):
    import pytest

    original = app.projects.create("Existing", repository=str(repository))
    unbound = app.projects.create("New")
    with pytest.raises(ValueError, match="已有活跃项目"):
        app.projects.update(unbound.id, "Changed", "", str(repository / "."))
    assert app.db.get(unbound.id) == unbound
    assert app.db.get(original.id) == original
    # The original owner can still edit its ordinary metadata.
    assert app.projects.update(original.id, "Renamed", "", str(repository)).name == "Renamed"
    app.projects.delete(original.id)
    assert app.projects.update(unbound.id, "New", "", str(repository)).repository == str(repository)
    with pytest.raises(ValueError, match="已有活跃项目"):
        app.projects.restore(original.id)


def test_concurrent_repository_rebindings_have_only_one_winner(app, repository):
    import threading

    projects = [app.projects.create("One"), app.projects.create("Two")]
    gate = threading.Barrier(2)

    def bind(project):
        gate.wait()
        try:
            return app.projects.update(project.id, project.name, "", str(repository)).id
        except ValueError as error:
            assert "已有活跃项目" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(bind, projects))
    assert sum(result is not None for result in results) == 1
    assert sum(p.repository == str(repository) for p in app.db.list_projects()) == 1


def test_database_restore_checks_repository_ownership_atomically(app, repository):
    import pytest

    archived = app.projects.create("Archived", repository=str(repository))
    app.projects.delete(archived.id)
    stale = app.db.get(archived.id)
    app.projects.create("New owner", repository=str(repository))
    stale.archived = False
    with pytest.raises(ValueError, match="已有活跃项目"):
        app.db.save(stale, "project_restored")
    assert app.db.get(archived.id).archived
