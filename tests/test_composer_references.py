import asyncio
import base64
import json
import sqlite3
from copy import deepcopy

import pytest
from evograph.application.agent_runtime import history_content
from evograph.domain.composer import ComposerDocument
from evograph.domain.models import ArchitectureSpec, PendingQuestion
from evograph.infrastructure.database import Database
from evograph.transport.desktop import DesktopBridge
from evograph.transport.http import AgentRequest, create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


def text(value):
    return {"type": "text", "text": value}


def reference(project, kind="milestone", id="M01", label="Login"):
    return {"type": "reference", "kind": kind, "id": id, "project_id": project.id, "label": label}


def document(*parts):
    return {"version": 1, "parts": list(parts)}


def send(app, project, doc=None, content=None, **kwargs):
    if content is None:
        content = ComposerDocument.model_validate(doc).plain_text()

    async def collect():
        return [
            event
            async for event in app.agent.stream(
                project.id, content, composer_document=doc, **kwargs
            )
        ]

    return asyncio.run(collect())


def provider_capture(app):
    requests = []

    async def provider(messages, schemas):
        requests.append(deepcopy(messages))
        yield {"type": "text", "text": "收到，合成测试未修改规划。"}

    app.settings.stream = provider
    return requests


def test_catalog_separates_current_object_kinds_and_legacy_materials(app, planned):
    app.design.update(
        planned.id,
        ArchitectureSpec(
            summary="Current target architecture",
            technologies=[{"area": "runtime", "choice": "Python", "rationale": "Existing"}],
            diagram={
                "id": "target",
                "title": "Target",
                "nodes": [{"id": "M01", "label": "Same ID, component", "role": "backend"}],
                "edges": [],
            },
        ),
    )
    p = app.db.get(planned.id)
    p.source_milestones = [
        p.milestones[0].model_copy(update={"origin": "source", "source_baseline_id": p.baseline.id})
    ]
    p.source_diagram = p.architectures[-1].diagram.model_copy(deep=True)
    p.source_diagram.nodes[0].label = "Observed source component"
    app.db.save(p, "fixture_source_objects")
    asset = app.attachments.upload(p.id, "notes.md", base64.b64encode(b"notes").decode())
    catalog = app.references.catalog(p.id)["items"]
    assert {row["kind"] for row in catalog} == {
        "milestone",
        "source_milestone",
        "architecture_component",
        "source_component",
        "attachment",
        "repository",
    }
    assert all(row["project_id"] == p.id for row in catalog)
    assert len([row for row in catalog if row["id"] == "M01"]) == 4
    assert any(row["id"] == asset.id for row in catalog)
    assert {row["kind"] for row in app.references.list(p.id)["items"]} == {
        "attachment",
        "repository",
    }
    bridge = DesktopBridge(app)
    assert bridge.command("references.catalog", {"project_id": p.id})["data"]["items"] == catalog


def test_canonical_rename_and_model_identity_do_not_rewrite_old_history(app, planned):
    requests = provider_capture(app)
    doc = document(text("补充 "), reference(planned, label="old client label"), text(" 的恢复行为"))
    result = send(app, planned, doc)
    assert result[-1]["summary"]["status"] == "completed"
    saved = app.db.messages(planned.id)[0]
    assert saved["content"] == "补充 #Login 的恢复行为"
    assert saved["composer_document"]["parts"][1]["label"] == "Login"
    assert doc["parts"][1]["label"] == "old client label"
    blocks = requests[0][-1]["content"]
    assert blocks[0]["text"] == saved["content"]
    data = json.loads(blocks[1]["text"].split("\n", 1)[1])
    assert data[0]["id"] == "M01" and data[0]["kind"] == "milestone"
    assert "not an edit scope" in blocks[1]["text"]
    p = app.db.get(planned.id)
    p.milestones[0].title = "Renamed current node"
    app.db.save(p, "fixture_rename")
    send(app, planned, doc)
    assert app.db.messages(p.id)[-2]["content"] == "补充 #Renamed current node 的恢复行为"
    assert app.db.messages(p.id)[0] == saved
    assert "Historical inline references" in history_content(saved)
    assert '"id": "M01"' in history_content(saved)
    assert Database(app.db.path).messages(p.id)[0] == saved


@pytest.mark.parametrize("invalid", ["foreign", "missing", "wrong_kind", "mismatch"])
def test_invalid_reference_never_consumes_question_or_calls_provider(app, planned, invalid):
    requests = provider_capture(app)
    p = app.db.get(planned.id)
    p.question = PendingQuestion(prompt="选择字段？", category="decision", options=["A", "B"])
    qid = p.question.id
    app.db.save(p, "fixture_question")
    token = reference(p)
    if invalid == "foreign":
        token["project_id"] = app.projects.create("Other").id
    elif invalid == "missing":
        token["id"] = "deleted"
    elif invalid == "wrong_kind":
        token["kind"] = "architecture_component"
    doc = document(token)
    result = send(
        app, p, doc, content="different text" if invalid == "mismatch" else None, question_id=qid
    )
    assert any(event["type"] == "error" for event in result)
    assert requests == []
    assert app.db.get(p.id).question.id == qid
    assert app.db.messages(p.id) == []


def test_latest_catalog_rejects_removed_milestone_and_retired_component(app, planned):
    requests = provider_capture(app)
    p = app.db.get(planned.id)
    p.milestones = []
    app.db.save(p, "fixture_deleted_node")
    result = send(app, p, document(reference(p)))
    assert any("已不存在" in e.get("message", "") for e in result)
    assert requests == []

    app.design.update(
        p.id,
        ArchitectureSpec(
            summary="First design",
            technologies=[{"area": "runtime", "choice": "Python", "rationale": "Existing"}],
            diagram={"id": "target", "title": "Target", "nodes": [{"id": "OLD", "label": "Old"}]},
        ),
    )
    p = app.db.get(p.id)
    latest = p.architectures[-1].model_copy(deep=True)
    latest.number += 1
    latest.diagram.nodes[0].id = "NEW"
    latest.diagram.nodes[0].label = "New"
    p.architectures.append(latest)
    app.db.save(p, "fixture_retired_component")
    result = send(app, p, document(reference(p, "architecture_component", "OLD", "Old")))
    assert any("已不存在" in e.get("message", "") for e in result)
    assert requests == []
    assert app.db.get(p.id).architectures[0].diagram.nodes[0].id == "OLD"


def test_canonical_rename_over_size_preserves_pending_question(app, planned):
    p = app.db.get(planned.id)
    p.milestones[0].title = "Long renamed title " * 6
    p.question = PendingQuestion(prompt="选择字段？", category="decision", options=["A", "B"])
    app.db.save(p, "fixture_rename_question")
    requests = provider_capture(app)
    doc = document(text("x" * 15990), reference(p, label="a"))
    result = send(app, p, doc, question_id=p.question.id)
    assert any(e["type"] == "error" for e in result)
    assert app.db.get(p.id).question.id == p.question.id
    assert app.db.messages(p.id) == [] and requests == []


def test_safe_repository_candidates_reject_traversal_hidden_and_symlink(app, planned, repository):
    (repository / ".env").write_text("never read this")
    (repository / "outside.py").symlink_to(repository.parent / "outside.py")
    requests = provider_capture(app)
    for id in ("repo:../outside.py", "repo:.env", "repo:outside.py", "https://example.com"):
        result = send(app, planned, document(reference(planned, "repository", id, "auth.py")))
        assert any(e["type"] == "error" for e in result)
    assert requests == []
    result = send(
        app, planned, document(reference(planned, "repository", "repo:auth.py", "misleading"))
    )
    assert result[-1]["summary"]["status"] == "completed"
    assert requests[0][-1]["content"][0]["text"] == "@auth.py"
    assert '"path": "auth.py"' in requests[0][-1]["content"][1]["text"]


def test_missing_repository_does_not_hide_saved_objects(app, planned, repository):
    repository.rename(repository.with_name("moved-repository"))
    catalog = app.references.catalog(planned.id)
    assert catalog["warnings"] and any(item["kind"] == "milestone" for item in catalog["items"])
    assert all(item["kind"] != "repository" for item in catalog["items"])
    requests = provider_capture(app)
    result = send(app, planned, document(reference(planned)))
    assert result[-1]["summary"]["status"] == "completed" and requests
    requests.clear()
    result = send(
        app, planned, document(reference(planned, "repository", "repo:auth.py", "auth.py"))
    )
    assert any(e["type"] == "error" for e in result) and requests == []


def test_inline_and_explicit_attachment_union_is_deduplicated_and_bounded(app, planned):
    assets = [
        app.attachments.upload(
            planned.id, f"spec-{i}.md", base64.b64encode(f"spec{i}".encode()).decode()
        )
        for i in range(7)
    ]
    requests = provider_capture(app)
    doc = document(reference(planned, "attachment", assets[0].id, "old-name"))
    result = send(app, planned, doc, attachment_ids=[asset.id for asset in assets[:6]])
    assert result[-1]["summary"]["status"] == "completed"
    blocks = requests[0][-1]["content"]
    assert len([b for b in blocks if b.get("text", "").startswith("Uploaded reference")]) == 6
    assert app.db.messages(planned.id)[0]["content"] == "@spec-0.md"
    requests.clear()
    result = send(app, planned, doc, attachment_ids=[asset.id for asset in assets[1:]])
    assert any("最多引用 6" in e.get("message", "") for e in result)
    assert requests == []


def test_inline_attachment_without_explicit_selection_reaches_provider(app, planned):
    asset = app.attachments.upload(
        planned.id, "only.md", base64.b64encode(b"selected by token").decode()
    )
    requests = provider_capture(app)
    send(app, planned, document(reference(planned, "attachment", asset.id, asset.name)))
    assert any("selected by token" in b.get("text", "") for b in requests[0][-1]["content"])


def test_plain_hash_at_and_old_clients_remain_plain_text(app, planned):
    requests = provider_capture(app)
    content = "literal #Login @auth.py and <script>untrusted text</script>"
    send(app, planned, content=content)
    assert requests[0][-1]["content"] == content
    assert app.db.messages(planned.id)[0]["composer_document"] is None


def test_failed_answer_keeps_canonical_document_and_new_question_blocks_retry(app, planned):
    p = app.db.get(planned.id)
    p.question = PendingQuestion(prompt="选择字段？", category="decision", options=["A", "B"])
    old = p.question.model_copy()
    app.db.save(p, "fixture_question")

    async def failed(messages, schemas):
        raise ValueError("simulated network failure")
        yield

    app.settings.stream = failed
    original = document(text("保留 "), reference(p))
    result = send(app, p, original, question_id=old.id)
    assert result[-1]["summary"]["status"] == "failed"
    assert app.db.get(p.id).question is None
    assert app.db.messages(p.id)[0]["composer_document"] == original
    p = app.db.get(p.id)
    p.question = PendingQuestion(prompt="是否公开？", category="decision", options=["是", "否"])
    app.db.save(p, "fixture_new_question_after_read")
    requests = provider_capture(app)
    retry = document(text('继续已保存的状态。原问题："选择字段？"\n原回答：\n'), *original["parts"])
    result = send(app, p, retry)
    assert any("先回答" in e.get("message", "") for e in result)
    assert app.db.get(p.id).question.id == p.question.id
    assert len(app.db.messages(p.id)) == 1 and requests == []


@pytest.mark.parametrize("mutation", ["version", "html", "url", "too_many", "mismatched_kind"])
def test_http_and_native_share_strict_document_validation(app, planned, mutation):
    doc = document(reference(planned))
    if mutation == "version":
        doc["version"] = 2
    elif mutation == "html":
        doc["parts"][0]["html"] = "<img onerror=alert(1)>"
    elif mutation == "url":
        doc["parts"][0]["url"] = "https://example.com"
    elif mutation == "too_many":
        doc["parts"] *= 33
    else:
        doc["parts"][0]["kind"] = "tool_command"
    params = {"project_id": planned.id, "content": "#Login", "composer_document": doc}
    client = TestClient(create_app(app))
    assert client.post("/api/agent/stream", json=params).status_code == 422
    with pytest.raises(ValidationError):
        DesktopBridge(app).start_agent("invalid-document", params)


def test_document_limits_and_unicode_plain_render(planned):
    doc = document(text("前缀🙂\n"), reference(planned, label="登录🙂"), text(" 后缀"))
    assert ComposerDocument.model_validate(doc).plain_text() == "前缀🙂\n#登录🙂 后缀"
    with pytest.raises(ValidationError):
        AgentRequest(
            project_id=planned.id, content="a", composer_document=document(text("a" * 16001))
        )
    with pytest.raises(ValidationError):
        ComposerDocument.model_validate(document(*[text("")] * 513))


def test_additive_message_migration_preserves_legacy_rows(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE messages(id TEXT PRIMARY KEY,project_id TEXT,role TEXT,content TEXT,created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO messages VALUES('old','p','user','original answer','2026-01-01')"
        )
    db = Database(path)
    assert db.messages("p") == [
        {
            "id": "old",
            "project_id": "p",
            "role": "user",
            "content": "original answer",
            "created_at": "2026-01-01",
            "composer_document": None,
        }
    ]
    assert Database(path).messages("p") == db.messages("p")
    with db.connect() as connection:
        # An overlapping old desktop binary still uses its five-column INSERT.
        connection.execute(
            "INSERT INTO messages VALUES('old-client','p','user','still works','2026-01-02')"
        )
    assert len(db.messages("p")) == 2


def test_message_document_commits_atomically_and_cascades_with_message(app, planned):
    with pytest.raises(TypeError):
        app.db.message(planned.id, "user", "must roll back", {"invalid": object()})
    assert app.db.messages(planned.id) == []
    doc = document(reference(planned))
    app.db.message(planned.id, "user", "#Login", doc)
    saved = app.db.messages(planned.id)[0]
    with app.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM message_documents").fetchone()[0] == 1
        connection.execute("DELETE FROM messages WHERE id=?", (saved["id"],))
        assert connection.execute("SELECT count(*) FROM message_documents").fetchone()[0] == 0


def test_valid_document_http_stream_and_catalog(app, planned):
    requests = provider_capture(app)
    client = TestClient(create_app(app))
    catalog = client.post(
        "/api/command", json={"action": "references.catalog", "params": {"project_id": planned.id}}
    ).json()
    assert catalog["ok"] and any(item["kind"] == "milestone" for item in catalog["data"]["items"])
    response = client.post(
        "/api/agent/stream",
        json={
            "project_id": planned.id,
            "content": "#old",
            "composer_document": document(reference(planned, label="old")),
        },
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert response.status_code == 200 and events[-1]["summary"]["status"] == "completed"
    assert requests[0][-1]["content"][0]["text"] == "#Login"
