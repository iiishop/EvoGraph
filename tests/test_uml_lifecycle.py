import asyncio

import pytest
from evograph.agent_tools.base import ToolContext
from evograph.agent_tools.repository import ReadFile, read_repository_file
from evograph.agent_tools.uml import save_uml
from evograph.application.uml_lifecycle import class_model_state
from evograph.domain.models import UmlDiagram

SOURCE = """@startuml
skinparam packageStyle rectangle
skinparam linetype polyline
hide empty members
package Authentication {
class Auth <<Module>> {
 + login() : bool
}
class FutureSession {
 + create() : str
}
Auth --> FutureSession : DESIGN Create >
}
@enduml"""


def diagram(**overrides):
    return UmlDiagram(
        **(
            dict(
                id="class_model",
                title="认证总览",
                kind="class",
                source=SOURCE,
                scope="认证能力",
                origin="mixed",
                source_refs=["auth.py"],
                design_elements=["FutureSession"],
            )
            | overrides
        )
    )


def test_mixed_diagram_read_receipts_markers_and_freshness(app, planned, monkeypatch, repository):
    monkeypatch.setattr("evograph.application.uml.render", lambda _: b"<svg/>")
    ctx = ToolContext(planned.id, app)
    with pytest.raises(ValueError, match="先读取"):
        save_uml(ctx, diagram())
    read_repository_file(ctx, ReadFile(path="auth.py"))
    save_uml(ctx, diagram())
    p = app.db.get(planned.id)
    saved = p.uml_diagrams[-1]
    assert "note right of FutureSession : DESIGN" in saved.source
    assert class_model_state(p)["current"]
    assert save_uml(ctx, saved.model_copy(deep=True))["status"] == "NO_PROGRESS"
    p.milestones[0].intent += "；增加新会话能力"
    app.db.save(p, "design_changed")
    assert class_model_state(p)["reasons"] == ["设计已更新"]
    (repository / "auth.py").write_text("def login(): return False\n")
    with pytest.raises(ValueError, match="源码已经变化"):
        save_uml(ctx, diagram())
    p = app.execution.refresh(p.id)
    assert "源码基线已更新" in class_model_state(p)["reasons"]


def test_markers_cannot_reference_unknown_entities_or_claim_source(app, planned, monkeypatch):
    monkeypatch.setattr("evograph.application.uml.render", lambda _: b"<svg/>")
    for overrides in (
        dict(design_elements=[]),
        dict(design_elements=["Absent"]),
        dict(origin="source"),
        dict(design_elements=["FutureSession\n!include bad"]),
    ):
        with pytest.raises(ValueError):
            app.uml.save(planned.id, diagram(**overrides))
    assert not app.db.get(planned.id).uml_diagrams


def test_implemented_design_drops_markers_and_keeps_history(app, planned, monkeypatch):
    monkeypatch.setattr("evograph.application.uml.render", lambda _: b"<svg/>")
    app.uml.save(planned.id, diagram())
    current = diagram(
        origin="source", design_elements=[], source=SOURCE.replace("DESIGN Create", "Create")
    )
    app.uml.save(planned.id, current)
    revisions = app.db.get(planned.id).uml_diagrams
    assert "DESIGN · 设计待实现" in revisions[0].source
    assert "DESIGN" not in revisions[-1].source
    assert revisions[-1].baseline_id == planned.baseline.id


def test_runtime_requests_class_sync_after_design_change(app, planned, monkeypatch):
    from conftest import proposal
    from test_agent_stream import tool_chunks

    monkeypatch.setattr("evograph.application.uml.render", lambda _: b"<svg/>")
    app.uml.save(planned.id, diagram())
    calls = 0

    async def stream(messages, schemas):
        nonlocal calls
        calls += 1
        if calls == 1:
            args = proposal().milestones[0].model_dump()
            args["intent"] = "新增会话设计"
            name = "update_milestone"
        elif calls == 2:
            yield {"type": "text", "text": "已更新规划"}
            return
        elif calls == 3:
            assert "class_model" in messages[-1]["content"]
            name, args = "read_repository_file", {"path": "auth.py"}
        elif calls == 4:
            name, args = "save_uml", diagram().model_dump()
        else:
            yield {"type": "text", "text": "类图已同步"}
            return
        async for item in tool_chunks(name, args):
            yield item

    app.settings.stream = stream

    async def run():
        return [e async for e in app.agent.stream(planned.id, "更新设计")]

    events = asyncio.run(run())
    assert not [e for e in events if e["type"] in {"error", "tool_failed"}]
    assert class_model_state(app.db.get(planned.id))["current"]
    assert len(app.db.get(planned.id).uml_diagrams) == 2


def test_design_labels_render_in_exported_image():
    import shutil
    from pathlib import Path

    from evograph.application.uml import render
    from evograph.application.uml_lifecycle import annotate_design

    if not shutil.which("java") or not Path(".tools/plantuml.jar").is_file():
        pytest.skip("Local PlantUML renderer unavailable")
    result = render(annotate_design(diagram()))
    assert b"DESIGN" in result and b"FutureSession" in result
