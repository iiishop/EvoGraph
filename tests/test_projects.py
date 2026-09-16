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
