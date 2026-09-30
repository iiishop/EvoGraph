import base64

from evograph.application.attachments import AttachmentService
from evograph.domain.models import Attachment


def test_same_content_reuses_id_but_changed_content_is_new(app, planned):
    content = base64.b64encode(b"requirements").decode()
    first = app.attachments.upload(planned.id, "spec.md", content)
    revision = app.db.get(planned.id).revision
    for name in ["spec.md", "copy.md", "pasted.txt"]:
        assert app.attachments.upload(planned.id, name, content).id == first.id
    assert app.db.get(planned.id).revision == revision
    assert len(app.db.get(planned.id).attachments) == 1
    changed = app.attachments.upload(planned.id, "spec.md", base64.b64encode(b"new").decode())
    assert changed.id != first.id
    other = app.projects.create("Other")
    assert app.attachments.upload(other.id, "spec.md", content).id != first.id


def test_legacy_duplicates_merge_and_remap_references(app, planned):
    blob = b"same content"
    first = app.attachments.upload(planned.id, "spec.txt", base64.b64encode(blob).decode())
    p = app.db.get(planned.id)
    duplicate = Attachment(name="copy.txt", media_type="text/plain", size=len(blob))
    p.attachments.append(duplicate)
    p.milestones[0].attachment_ids = [duplicate.id, first.id]
    app.db.save(p, "test_legacy")
    with app.db.connect() as db:
        db.execute("INSERT INTO attachments VALUES(?,?,?)", (duplicate.id, p.id, blob))
    AttachmentService(app.db)
    p = app.db.get(p.id)
    assert [a.id for a in p.attachments] == [first.id]
    assert p.milestones[0].attachment_ids == [first.id]
    revision = p.revision
    AttachmentService(app.db)
    assert app.db.get(p.id).revision == revision


def test_reference_catalog_contains_assets_and_safe_repository_paths(app, planned, repository):
    (repository / "WorldWildWeb.py").write_text("pass")
    (repository / ".env").write_text("private")
    (repository / "node_modules").mkdir()
    (repository / "node_modules" / "hidden.js").write_text("ignored")
    asset = app.attachments.upload(planned.id, "spec.txt", base64.b64encode(b"spec").decode())
    items = app.references.list(planned.id)["items"]
    assert any(item["id"] == asset.id for item in items)
    paths = {item["path"] for item in items if item["kind"] == "repository"}
    assert "WorldWildWeb.py" in paths
    assert ".env" not in paths
    assert "node_modules/hidden.js" not in paths
    assert app.references.list(app.projects.create("No repo").id)["items"] == []


def test_concurrent_uploads_share_one_asset(app, planned):
    from concurrent.futures import ThreadPoolExecutor

    content = base64.b64encode(b"repeated drop").decode()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assets = list(
            pool.map(lambda _: app.attachments.upload(planned.id, "spec.txt", content), range(4))
        )
    assert len({a.id for a in assets}) == 1
    assert len(app.db.get(planned.id).attachments) == 1
