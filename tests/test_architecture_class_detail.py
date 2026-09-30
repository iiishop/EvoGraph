import asyncio
import hashlib
from pathlib import Path

import pytest
from evograph.application.class_detail import MAX_FILE_BYTES, validate_class_source
from evograph.application.uml_lifecycle import class_model_state
from evograph.domain.models import (
    ArchitectureRevision,
    Diagram,
    DiagramEdge,
    DiagramNode,
    UmlDiagram,
)


@pytest.fixture
def scoped(app, repository, monkeypatch):
    (repository / "auth.py").write_text(
        "from outside import SecretClass\n"
        "class Base:\n    id: int\n"
        "class Auth(Base):\n    token: str\n"
        "    def login(self, user: str, password: str = 'do-not-copy') -> bool:\n"
        "        self.session: str = 'not-a-field-default'\n        return True\n"
    )
    (repository / "outside.py").write_text("class SecretClass:\n    unseen: str\n")
    p = app.projects.create("Scoped", repository=str(repository))
    p = app.execution.refresh(p.id)
    p.source_diagram = Diagram(
        id="source_architecture",
        title="Source",
        nodes=[
            DiagramNode(id="auth", label="Authentication", source_refs=["auth.py"]),
            DiagramNode(id="outside", label="External service", source_refs=["outside.py"]),
        ],
        edges=[DiagramEdge(source="auth", target="outside", label="Import")],
    )
    app.db.save(p, "test_scoped")
    calls = []
    monkeypatch.setattr(
        "evograph.application.uml.render", lambda source: calls.append(source) or b"<svg/>"
    )
    return p, calls


def detail(app, p, **kwargs):
    return app.uml.class_detail(p.id, kwargs.pop("component_ids", ["auth"]), **kwargs)


def test_only_selected_file_is_read_and_extracted(app, scoped, repository, monkeypatch):
    p, calls = scoped
    original = Path.open
    reads = []

    def tracked(path, *args, **kwargs):
        if path.is_relative_to(repository):
            reads.append(path.name)
            assert path.name == "auth.py", "Outside class detail must never be read"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked)
    result = detail(app, p)
    assert result["status"] == "ready"
    assert result["files"] == ["auth.py"]
    assert result["component_ids"] == ["auth"]
    assert set(reads) == {"auth.py"}
    assert len(calls) == 1
    source = result["diagram"]["source"]
    assert "left to right direction" in source  # Keep dependency boundaries in a vertical stack.
    assert 'class "Auth"' in source and "login(self, user: str" in source
    assert "unseen" not in source and 'class "SecretClass"' not in source
    assert "do-not-copy" not in source and "not-a-field-default" not in source
    assert "&gt; bool" not in source  # Return type represented as a UML type.
    assert "<<Boundary>>" in source and "SRC Extend" in source
    assert result["diagram"]["source_fingerprints"] == {
        "auth.py": hashlib.sha256((repository / "auth.py").read_bytes()).hexdigest()
    }
    assert not app.db.get(p.id).uml_diagrams  # Read-only drill-down does not mutate history.


def test_contract_exact_revision_stable_ids_and_no_design_invention(app, scoped):
    p, calls = scoped
    p.architectures = [
        ArchitectureRevision(
            number=4,
            summary="Design",
            technologies=[dict(area="api", choice="Python", rationale="Existing")],
            diagram=Diagram(
                id="planned",
                title="Target",
                nodes=[
                    DiagramNode(id="auth", label="Mapped design", source_refs=["auth.py"]),
                    DiagramNode(id="future", label="Future service"),
                ],
                edges=[DiagramEdge(source="auth", target="future", label="Proposed request")],
            ),
        )
    ]
    app.db.save(p, "test_design")
    source = detail(app, p)
    target = detail(app, p, architecture_revision=4)
    assert target["status"] == "ready" and "DESIGN" in target["diagram"]["source"]
    assert source["diagram"]["id"] != target["diagram"]["id"]
    assert target["diagram"]["id"] == detail(app, p, architecture_revision=4)["diagram"]["id"]
    count = len(calls)
    assert detail(app, p, architecture_revision=3)["status"] == "unmapped"
    unmapped = detail(app, p, component_ids=["future"], architecture_revision=4)
    assert unmapped["status"] == "unmapped" and "DESIGN" in unmapped["message"]
    assert len(calls) == count


@pytest.mark.parametrize("case", ["components", "files", "classes", "members", "bytes", "imports"])
def test_oversized_scope_rejected_before_render(app, scoped, repository, case):
    p, calls = scoped
    ids = ["auth"]
    if case == "components":
        ids = ["auth", "outside", "extra", "more"]
    elif case == "files":
        for i in range(13):
            (repository / f"part{i}.py").write_text(f"class Part{i}: pass\n")
    elif case == "classes":
        (repository / "auth.py").write_text("\n".join(f"class Class{i}: pass" for i in range(25)))
    elif case == "members":
        (repository / "auth.py").write_text(
            "class Auth:\n" + "\n".join(f"    f{i}: str" for i in range(33))
        )
    elif case == "bytes":
        (repository / "auth.py").write_text("#" * (MAX_FILE_BYTES + 1))
    else:
        (repository / "auth.py").write_text(
            "\n".join(f"import mod{i}" for i in range(25)) + "\nclass Auth: pass\n"
        )
    refreshed = app.execution.refresh(p.id)
    if case == "files":
        p.source_diagram.nodes[0].source_refs = [f"part{i}.py" for i in range(13)]
    refreshed.source_diagram = p.source_diagram
    app.db.save(refreshed, "test_scope")
    result = detail(app, p, component_ids=ids)
    assert result["status"] == "too_large"
    assert not calls


def test_file_narrowing_has_distinct_scope_and_no_outside_file_read(app, scoped, repository):
    p, _ = scoped
    p.source_diagram.nodes[0].source_refs += ["outside.py"]
    app.db.save(p, "two_files")
    full = detail(app, p)
    narrow = detail(app, p, file_paths=["auth.py"])
    assert full["status"] == narrow["status"] == "ready"
    assert full["diagram"]["id"] != narrow["diagram"]["id"]
    assert narrow["files"] == ["auth.py"]
    assert "局部范围" in narrow["limitations"][0]
    assert detail(app, p, file_paths=["missing.py"])["status"] == "unmapped"
    assert detail(app, p, file_paths=[])["status"] == "unmapped"
    p = app.db.get(p.id)
    p.source_diagram.nodes[0].source_ref_count = 80
    app.db.save(p, "truncated_refs")
    assert detail(app, p)["status"] == "too_large"
    assert detail(app, p, file_paths=["auth.py"])["status"] == "ready"


@pytest.mark.parametrize(
    "case", ["empty", "unsupported", "syntax", "changed", "missing", "legacy_index"]
)
def test_unavailable_detail_does_not_render(app, scoped, repository, case):
    p, calls = scoped
    expected = "unmapped"
    if case == "empty":
        (repository / "auth.py").write_text("def login(): return True\n")
        expected = "empty"
    elif case == "unsupported":
        (repository / "auth.go").write_text("package auth\n")
        p.source_diagram.nodes[0].source_refs = ["auth.go"]
        expected = "unsupported"
    elif case == "syntax":
        (repository / "auth.py").write_text("class Bad syntax!\n")
        expected = "unsupported"
    elif case == "changed":
        (repository / "auth.py").write_text("class Changed: pass\n")
    elif case == "missing":
        (repository / "auth.py").unlink()
    else:
        p.source_file_fingerprints = {}
    if case in {"empty", "unsupported", "syntax"}:
        refreshed = app.execution.refresh(p.id)
        refreshed.source_diagram = p.source_diagram
        p = refreshed
    app.db.save(p, "test_case")
    result = detail(app, p)
    assert result["status"] == expected
    assert not calls


def test_render_time_source_change_is_rejected(app, scoped, repository, monkeypatch):
    p, _ = scoped

    def render(source):
        (repository / "auth.py").write_text("class Changed: pass\n")
        return b"<svg/>"

    monkeypatch.setattr("evograph.application.uml.render", render)
    result = detail(app, p)
    assert result["status"] == "unmapped" and "已经变化" in result["message"]
    assert "image" not in result


def test_legacy_diagrams_preserved_but_cannot_render_or_save_global(app, scoped):
    p, calls = scoped
    legacy = UmlDiagram(
        id="class_model",
        title="Legacy",
        kind="class",
        source="@startuml\nclass Legacy\n@enduml",
        scope="global",
    )
    p.uml_diagrams.append(legacy)
    app.db.save(p, "legacy_import")
    assert class_model_state(app.db.get(p.id))["required"] is False
    assert class_model_state(app.db.get(p.id))["current"] is True
    with pytest.raises(ValueError, match="历史全局"):
        app.uml.preview(p.id, "class_model")
    with pytest.raises(ValueError, match="全局 class_model"):
        app.uml.save(p.id, legacy)
    with pytest.raises(ValueError, match="选择"):
        app.uml.save(p.id, legacy.model_copy(update={"id": "another_global"}))
    assert not calls and len(app.db.get(p.id).uml_diagrams) == 1


def test_scoped_save_rejects_outside_refs_and_oversized_members(app, scoped):
    p, calls = scoped
    source = "@startuml\nskinparam packageStyle rectangle\nskinparam linetype polyline\nhide empty members\nclass Local\n@enduml"
    d = UmlDiagram(
        id="local",
        title="Local",
        kind="class",
        source=source,
        scope="auth",
        component_ids=["auth"],
        origin="source",
        source_refs=["outside.py"],
    )
    with pytest.raises(ValueError, match="文件范围"):
        app.uml.save(p.id, d)
    for body in [
        "class Huge { " + "; ".join(f"+ f{i}" for i in range(33)) + " }",
        "class Huge {\n" + "\n".join(f"+ f{i}" for i in range(33)) + "\n}",
    ]:
        with pytest.raises(ValueError):
            validate_class_source("@startuml\n" + body + "\n@enduml")
    assert not calls


def test_dispatch_contract_and_legacy_index_refresh(app, scoped):
    p, calls = scoped
    response = asyncio.run(
        app.dispatch(
            "architecture.class_detail",
            {"project_id": p.id, "component_ids": ["auth"], "architecture_revision": 0},
        )
    )
    assert response["ok"] and response["data"]["status"] == "ready"
    p.source_file_fingerprints = {}
    app.db.save(p, "old_index")
    refreshed = app.execution.refresh(p.id)
    assert refreshed.source_file_fingerprints
    assert len(refreshed.baselines) == len(p.baselines)


@pytest.mark.parametrize(
    "filename,content,signature",
    [
        (
            "order.hpp",
            "#include <string>\nstruct Order { int id; };\nclass Service { public: bool submit(const Order& order); };\n",
            "submit(const Order&amp; order)",
        ),
        (
            "service.ts",
            "export interface Order { id: number; }\nexport class Service { submit(order: Order): boolean { return true; } }",
            "submit(order : Order)",
        ),
        (
            "Service.vue",
            '<template><main>Example</main></template><script lang="ts">export class Service { submit(order: string): boolean { return true; } }</script>',
            "submit(order : string)",
        ),
    ],
)
def test_multilanguage_class_detail_with_real_renderer(
    app, repository, filename, content, signature
):
    import shutil

    if not shutil.which("java") or not Path(".tools/plantuml.jar").is_file():
        pytest.skip("Local PlantUML renderer unavailable")
    (repository / filename).write_text(content)
    p = app.projects.create("Languages", repository=str(repository))
    p = app.execution.refresh(p.id)
    node = next(n for n in p.source_diagram.nodes if filename in n.source_refs)
    result = app.uml.class_detail(p.id, [node.id], file_paths=[filename])
    assert result["status"] == "ready", result["message"]
    assert signature in result["diagram"]["source"]
    assert result["image"].startswith("data:image/svg+xml;base64,")
    assert result["limitations"]


def test_class_content_cannot_bypass_scoping_via_sequence_kind(app, scoped):
    p, calls = scoped
    disguised = UmlDiagram(
        id="global_sequence",
        title="Global",
        kind="sequence",
        source="@startuml\nclass Global\n@enduml",
        scope="global",
    )
    with pytest.raises(ValueError, match="class 类型"):
        app.uml.save(p.id, disguised)
    p.uml_diagrams.append(disguised)
    app.db.save(p, "legacy_wrong_kind")
    with pytest.raises(ValueError, match="历史类图"):
        app.uml.preview(p.id, disguised.id)
    assert not calls


def test_incomplete_whole_baseline_allows_verified_local_files_only(app, scoped):
    p, calls = scoped
    p.baselines[-1].complete = False
    app.db.save(p, "partial_binary_scan")
    result = detail(app, p)
    assert result["status"] == "ready"
    assert any("基线扫描不完整" in text for text in result["limitations"])
    assert "基线扫描不完整" in result["diagram"]["source"]
    count = len(calls)
    p = app.db.get(p.id)
    p.source_file_fingerprints.pop("auth.py")
    app.db.save(p, "missing_local_hash")
    assert detail(app, p)["status"] == "unmapped"
    assert len(calls) == count


def test_python_does_not_invent_members_from_nested_local_classes():
    from evograph.application.class_detail import parse_python

    parsed = parse_python(
        "local.py",
        """class Outer:
    def factory(self):
        self.actual: int = 1
        class Nested:
            def __init__(self):
                self.not_outer: str = 'nested'
        def nested_function(self):
            self.also_not_outer = True
""",
    )
    assert len(parsed["classes"]) == 1
    members = parsed["classes"][0]["members"]
    assert "+ actual : int" in members
    assert not any("not_outer" in member for member in members)


def test_boundary_becomes_internal_package_dependency_without_expansion(
    app, scoped, repository, monkeypatch
):
    from evograph.application.class_detail import alias

    p, _ = scoped
    # A third imported file remains a boundary even while two components are selected.
    (repository / "third.py").write_text("class NeverExpanded: pass\n")
    outside = repository / "outside.py"
    outside.write_text("import third\nclass SecretClass:\n    unseen: str\n")
    refreshed = app.execution.refresh(p.id)
    refreshed.source_diagram = p.source_diagram
    p = app.db.save(refreshed, "test_internal_edges")
    original = Path.open
    reads = set()

    def tracked(path, *args, **kwargs):
        if path.is_relative_to(repository):
            reads.add(path.name)
            assert path.name in {"auth.py", "outside.py"}
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked)
    single = detail(app, p)
    assert any("External service" in label for label in single["boundaries"])
    assert reads == {"auth.py"}
    multiple = detail(app, p, component_ids=["auth", "outside"])
    assert multiple["status"] == "ready"
    dependency = f"{alias('P_', 'auth')} ..> {alias('P_', 'outside')} : SRC Import"
    assert dependency in multiple["diagram"]["source"]
    assert not any("External service" in label for label in multiple["boundaries"])
    assert any("third" in label for label in multiple["boundaries"])
    assert 'class "NeverExpanded"' not in multiple["diagram"]["source"]
    assert reads == {"auth.py", "outside.py"}


def test_internal_design_relation_retains_design_provenance(app, scoped):
    from evograph.application.class_detail import alias

    p, _ = scoped
    diagram = p.source_diagram.model_copy(deep=True)
    diagram.edges[0].label = "Proposed persistence"
    p.architectures.append(
        ArchitectureRevision(
            number=1,
            summary="Proposed design",
            technologies=[dict(area="storage", choice="Python", rationale="Existing")],
            diagram=diagram,
        )
    )
    app.db.save(p, "test_internal_design")
    result = detail(app, p, component_ids=["auth", "outside"], architecture_revision=1)
    source = result["diagram"]["source"]
    endpoints = f"{alias('P_', 'auth')} ..> {alias('P_', 'outside')}"
    assert endpoints + " : DESIGN Proposed persistence" in source
    assert endpoints + " : SRC Import" in source
    assert "Invoke" not in source


@pytest.mark.parametrize(
    "source,target,files,expected",
    [
        ("orders.py", "storage", ["orders.py", "storage.py"], "storage.py"),
        ("pkg/orders.py", ".storage", ["pkg/orders.py", "pkg/storage.py"], "pkg/storage.py"),
        ("pkg/nested/orders.py", "..storage", ["pkg/storage.py"], "pkg/storage.py"),
        ("orders/service.ts", "../storage/repo", ["storage/repo.ts"], "storage/repo.ts"),
        ("orders/Service.vue", "../storage/repo", ["storage/repo.ts"], "storage/repo.ts"),
        ("native/order.hpp", "repo.hpp", ["native/repo.hpp"], "native/repo.hpp"),
        ("service.ts", "repo", ["repo.ts"], None),
        ("service.ts", "./repo", ["repo.ts", "repo.js"], None),
        ("service.py", "storage", ["storage.py", "storage/__init__.py"], None),
    ],
)
def test_import_resolution_matches_only_explicit_selected_paths(source, target, files, expected):
    from evograph.application.class_detail import selected_import

    assert selected_import(source, target, files) == expected
