import base64
import io
import zipfile

import pytest
from conftest import proposal
from evograph.domain.models import ArchitectureSpec, Diagram
from evograph.providers.streaming import anthropic_messages
from PIL import Image


def architecture():
    return ArchitectureSpec(
        summary="Frontend talks to API",
        technologies=[{"area": "API", "choice": "Python", "rationale": "Reuse backend modules"}],
        decisions=["Keep storage isolated"],
        diagram=Diagram(id="system", title="Architecture", nodes=[{"id": "api", "label": "API"}]),
    )


def test_architecture_revisions_require_review_and_valid_component_mapping(app, planned):
    app.design.update(planned.id, architecture())
    p = app.db.get(planned.id)
    assert p.architectures[-1].number == 1
    assert p.milestones[0].architecture_revision == 1
    assert any(o.id == "architecture" and not o.resolved for o in p.milestones[0].obligations)
    node = proposal().milestones[0]
    with pytest.raises(ValueError, match="关联"):
        app.graph.upsert(p.id, node, False)
    node.architecture_components = ["api"]
    app.graph.upsert(p.id, node, False)
    different = architecture()
    different.diagram.nodes[0].id = "removed"
    with pytest.raises(ValueError, match="retirements"):
        app.design.update(p.id, different)
    assert len(app.db.get(p.id).architectures) == 1


def test_state_cycles_allowed_but_unknown_endpoints_rejected(app, planned):
    diagram = Diagram(
        id="states",
        title="Session",
        kind="state",
        nodes=[{"id": "in", "label": "In"}, {"id": "out", "label": "Out"}],
        edges=[
            {"source": "in", "target": "out", "label": "logout"},
            {"source": "out", "target": "in", "label": "login"},
        ],
    )
    app.design.save_diagram(planned.id, diagram)
    diagram.edges[0].target = "missing"
    with pytest.raises(ValueError):
        app.design.save_diagram(planned.id, diagram)
    assert len(app.db.get(planned.id).diagrams) == 1


def test_upload_documents_and_images_scoped_and_multimodal(app, planned):
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(data, format="PNG")
    image = app.attachments.upload(
        planned.id, "diagram.png", base64.b64encode(data.getvalue()).decode()
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="urn:w"><w:t>Design requirements</w:t></w:document>',
        )
    document = app.attachments.upload(
        planned.id, "spec.docx", base64.b64encode(archive.getvalue()).decode()
    )
    assert document.excerpt == "Design requirements"
    blocks = app.attachments.context(planned.id, [document.id, image.id])
    converted = anthropic_messages([{"role": "user", "content": blocks}])
    assert converted[0]["content"][-1]["source"]["media_type"] == "image/png"
    other = app.projects.create("Other")
    with pytest.raises(ValueError):
        app.attachments.read(other.id, image.id)
    with pytest.raises(ValueError):
        app.attachments.upload(planned.id, "bad.png", base64.b64encode(b"not an image").decode())
    assert len(app.db.get(planned.id).attachments) == 2


def test_duplicate_dependency_is_rejected(app, planned):
    node = proposal().milestones[0]
    node.id = "M02"
    node.behaviors[0].key = "other.behavior"
    node.dependencies = ["M01", "M01"]
    node.dependency_reasons = {"M01": "Needs implementation"}
    with pytest.raises(ValueError, match="重复"):
        app.graph.upsert(planned.id, node, True)
    assert len(app.db.get(planned.id).milestones) == 1


def test_architecture_change_invalidates_existing_evidence(app, planned):
    from evograph.domain.models import Evidence
    from evograph.domain.policies import acceptance

    p = app.db.get(planned.id)
    p.evidence.append(
        Evidence(
            milestone_id="M01",
            behavior_revision_ids=p.milestones[0].behavior_revision_ids,
            baseline_id=p.baseline.id,
            fingerprint=p.baseline.fingerprint,
            command=["fixture"],
            result="PASS",
            output="ok",
            duration=0,
        )
    )
    p.milestones[0].status = "VERIFIED_COMPLETE"
    app.db.save(p, "fixture")
    assert acceptance(p)["achieved"]
    app.design.update(p.id, architecture())
    current = app.projects.get(p.id)
    assert not current["acceptance"]["achieved"]
    assert current["evidence_validity"][p.evidence[0].id] == "STALE"
    assert current["milestones"][0]["status"] == "REVALIDATION_REQUIRED"
