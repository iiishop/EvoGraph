"""Typed class detail is bounded syntax evidence, never a reconstructed runtime model."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from evograph.application import class_detail
from evograph.application.class_extractors import parse_classes
from evograph.application.uml import UmlRenderError, render
from evograph.domain.models import ArchitectureRevision, Diagram, DiagramEdge, DiagramNode


@pytest.fixture(autouse=True)
def local_renderer(monkeypatch):
    monkeypatch.setattr("evograph.application.uml.render", lambda source: b"<svg/>")


def make_project(app, repository, files, *, nodes=None, edges=()):
    for name, content in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    project = app.projects.create("Semantic source", repository=str(repository))
    project = app.execution.refresh(project.id)
    project.source_diagram = Diagram(
        id="source_architecture",
        title="Source",
        nodes=nodes or [DiagramNode(id="selected", label="Selected", source_refs=list(files))],
        edges=list(edges),
    )
    return app.db.save(project, "test_semantic_scope")


def detail(app, project, component_ids=None, **kwargs):
    return app.uml.class_detail(project.id, component_ids or ["selected"], **kwargs)


def refresh(app, project):
    current = app.execution.refresh(project.id)
    current.source_diagram = project.source_diagram
    return app.db.save(current, "test_semantic_refresh")


def test_semantic_contract_has_provenance_locations_typed_ids_and_canonical_text(app, repository):
    source = (
        "import external\n"
        "class Base: pass\n"
        "class Account(Base):\n"
        "    token: str = 'FIELD_SECRET'\n"
        "    @classmethod\n"
        "    async def load(cls, key: str = 'DEFAULT_SECRET') -> bool:\n"
        "        self.session: str = 'INSTANCE_SECRET'\n"
        "        return True\n"
    )
    project = make_project(app, repository, {"account.py": source})
    result = detail(app, project)
    assert result["status"] == "ready"
    assert result["image"].startswith("data:image/svg+xml;base64,")
    assert result["render_status"] == "ready" and "render_error" not in result
    model = result["semantic"]
    assert model["schema_version"] == 1 and model["origin"] == "source"
    assert model["project_id"] == project.id
    assert model["baseline_id"] == project.baseline.id
    assert model["architecture_revision"] == 0
    assert model["component_ids"] == ["selected"]
    assert model["files"] == ["account.py"]
    assert model["source_fingerprints"] == {
        "account.py": hashlib.sha256(source.encode()).hexdigest()
    }
    assert model["limitations"] == result["limitations"]
    base, account = model["classes"]
    assert account["kind"] == "class" and account["language"] == "python"
    assert account["location"] == {"path": "account.py", "line": 3, "end_line": 8}
    assert account["package_ids"] == [model["packages"][0]["id"]]
    members = {member["name"]: member for member in account["members"]}
    method = members["load"]
    assert method["text"] == "classmethod async load(cls, key: str = …) : bool"
    assert method["kind"] == "method" and method["visibility"] == "public"
    assert method["qualifiers"] == ["classmethod", "async"]
    assert method["location"] == {"path": "account.py", "line": 6, "end_line": 8}
    assert members["session"]["kind"] == "field"
    assert members["session"]["location"]["line"] == 7
    extends = next(edge for edge in model["relations"] if edge["kind"] == "extends")
    assert extends["source"] == account["id"] and extends["target"] == base["id"]
    assert extends["resolution"] == "selected" and extends["origin"] == "source"
    for group, prefix in (
        ("classes", "class_"),
        ("packages", "package_"),
        ("boundaries", "boundary_"),
        ("relations", "relation_"),
    ):
        assert all(item["id"].startswith(prefix) for item in model[group])
    assert all(member["id"].startswith("member_") for member in account["members"])
    assert "SECRET" not in json.dumps(result)
    assert not app.db.get(project.id).uml_diagrams


def test_typed_model_uses_one_parse_and_only_explicit_selected_files(app, repository, monkeypatch):
    project = make_project(
        app,
        repository,
        {
            "a.py": "from outside import NeverRead\nclass A:\n    def use(self): pass\n",
            "outside.py": "class NeverRead: pass\n",
        },
        nodes=[DiagramNode(id="selected", label="A", source_refs=["a.py"])],
    )
    original_open, original_parse = Path.open, class_detail.parse_python
    reads, parses = [], []

    def tracked_open(path, *args, **kwargs):
        if path.is_relative_to(repository):
            reads.append(path.name)
            assert path.name == "a.py"
        return original_open(path, *args, **kwargs)

    def tracked_parse(path, text):
        parses.append(path)
        return original_parse(path, text)

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(class_detail, "parse_python", tracked_parse)
    model = detail(app, project)["semantic"]
    assert parses == ["a.py"] and set(reads) == {"a.py"}
    assert [item["name"] for item in model["classes"]] == ["A"]
    assert model["classes"][0]["members"][0]["text"] == "use(self)"
    assert any(item["kind"] == "import" for item in model["boundaries"])


def test_typescript_relations_qualifiers_and_function_typed_fields(app, repository):
    project = make_project(
        app,
        repository,
        {
            "model.ts": """interface Input { value: string; }
class Base {}
class Model extends Base implements Input {
    static readonly value: string = 'FIELD_SECRET';
    handler: (input: Input) => void;
    constructor(private readonly key: string = 'PARAM_SECRET') {}
    protected async run(input: Input): Promise<void> {}
}
"""
        },
    )
    result = detail(app, project)
    model = result["semantic"]
    by_name = {item["name"]: item for item in model["classes"]}
    owner = by_name["Model"]
    members = {member["name"]: member for member in owner["members"]}
    assert members["value"]["qualifiers"] == ["static", "readonly"]
    assert members["value"]["text"] == "static readonly value : string = …"
    assert members["handler"]["kind"] == "field"
    assert members["handler"]["text"] == "handler : (input: Input) => void"
    assert members["run"]["text"] == "async run(input : Input) : Promise<void>"
    assert members["run"]["visibility"] == "protected"
    assert members["key"]["qualifiers"] == ["readonly"]
    assert members["key"]["visibility"] == "private"
    assert members["key"]["text"] == "readonly key : string = …"
    assert members["constructor"]["text"] == "constructor(private readonly key : string = …)"
    relations = {
        (edge["source"], edge["target"], edge["kind"], edge["resolution"])
        for edge in model["relations"]
    }
    assert relations == {
        (owner["id"], by_name["Base"]["id"], "extends", "selected"),
        (owner["id"], by_name["Input"]["id"], "implements", "selected"),
    }
    assert "..|>" in result["diagram"]["source"] and "SRC Implement" in result["diagram"]["source"]
    assert "SECRET" not in json.dumps(result)


def test_cpp_typed_members_keep_pointer_fields_and_recognized_qualifiers(app, repository):
    project = make_project(
        app,
        repository,
        {
            "model.hpp": """namespace Domain {
struct Base {};
struct Model : Base {
    static constexpr int limit = 938;
    void (*callback)(int code);
    virtual bool run(int count = 728) const noexcept;
};
}
"""
        },
    )
    model = detail(app, project)["semantic"]
    owner = next(item for item in model["classes"] if item["name"] == "Domain::Model")
    assert owner["language"] == "cpp" and owner["kind"] == "struct"
    members = {member["name"]: member for member in owner["members"]}
    assert members["callback"]["kind"] == "field"
    assert members["callback"]["text"] == "callback : void (*)(int code)"
    assert members["run"]["qualifiers"] == ["virtual", "const", "noexcept"]
    assert members["run"]["text"] == "virtual run(int count = …) : bool const noexcept"
    assert members["limit"]["qualifiers"] == ["static", "constexpr"]
    assert members["limit"]["text"] == "static constexpr limit : int = …"
    assert model["relations"][0]["resolution"] == "selected"
    # Opaque identities/digests can coincidentally contain a short numeric
    # sentinel (for example relation_e728...). Keep every content/display
    # field in this privacy check, including source paths and limitations.
    content = json.loads(json.dumps(model))
    content.pop("project_id")
    content.pop("baseline_id")
    content["source_fingerprints"] = list(content["source_fingerprints"])
    for group in ("classes", "packages", "boundaries", "relations"):
        for item in content[group]:
            item.pop("id")
            if group == "classes":
                item.pop("package_ids")
                for member in item["members"]:
                    member.pop("id")
            elif group == "relations":
                item.pop("source")
                item.pop("target")
    assert "938" not in json.dumps(content) and "728" not in json.dumps(content)


def test_vue_source_ranges_refer_to_original_file_and_script_language():
    result = parse_classes(
        "View.vue",
        """<template><div /></template>
<script lang="ts">
export class State {
  readonly value: string = 'FIELD_SECRET';
  run(value = 'PARAM_SECRET'): void {}
}
</script>
<script>class Script { count = 324; }</script>
""",
    )
    first, second = result["classes"]
    assert (first["line"], first["end_line"], first["language"]) == (3, 6, "typescript")
    assert [(m["line"], m["end_line"]) for m in first["member_declarations"]] == [(4, 4), (5, 5)]
    assert second["language"] == "javascript" and second["line"] == 8
    assert "SECRET" not in json.dumps(result)


def test_stable_ids_survive_scope_changes_line_moves_but_not_signature_rebinding(app, repository):
    source = "class A:\n    def run(self, value: str): pass\n"
    project = make_project(
        app,
        repository,
        {"a.py": source, "b.py": "class B: pass\n"},
        nodes=[
            DiagramNode(id="selected", label="One", source_refs=["a.py"]),
            DiagramNode(id="second", label="Two", source_refs=["a.py", "b.py"]),
        ],
    )
    original = detail(app, project)["semantic"]["classes"][0]
    full = detail(app, project, ["second", "selected"])["semantic"]
    full_class = next(item for item in full["classes"] if item["name"] == "A")
    assert original["id"] == full_class["id"]
    assert len(full_class["package_ids"]) == 2
    assert original["members"][0]["id"] == full_class["members"][0]["id"]
    assert full == detail(app, project, ["selected", "second"])["semantic"]
    (repository / "a.py").write_text("# move declarations down\n\n" + source)
    project = refresh(app, project)
    moved = detail(app, project)["semantic"]["classes"][0]
    assert original["id"] == moved["id"]
    assert original["members"][0]["id"] == moved["members"][0]["id"]
    (repository / "a.py").write_text(source.replace("value: str", "value: int"))
    project = refresh(app, project)
    changed = detail(app, project)["semantic"]["classes"][0]
    assert original["id"] == changed["id"]
    assert original["members"][0]["id"] != changed["members"][0]["id"]


def test_ambiguous_and_unresolved_bases_never_bind_arbitrary_classes(app, repository):
    project = make_project(
        app,
        repository,
        {
            "a.py": """if flag:
    class Base: pass
else:
    class Base: pass
class A(Base, External): pass
""",
            "b.py": "class External: pass\n",
        },
    )
    model = detail(app, project)["semantic"]
    bases = [item for item in model["classes"] if item["name"] == "Base"]
    assert len({item["id"] for item in bases}) == 2
    reasons = {item["label"]: item["reason"] for item in model["boundaries"]}
    assert reasons == {"Base · Base": "ambiguous", "Base · External": "unresolved"}
    assert all(edge["resolution"] == "boundary" for edge in model["relations"])


def test_architecture_design_edges_remain_separate_from_source_imports(app, repository):
    project = make_project(
        app,
        repository,
        {"a.py": "import b\nclass A: pass\n", "b.py": "class B: pass\n"},
        nodes=[
            DiagramNode(id="selected", label="A", source_refs=["a.py"]),
            DiagramNode(id="second", label="B", source_refs=["b.py"]),
            DiagramNode(id="future", label="Future"),
        ],
        edges=[
            DiagramEdge(source="selected", target="second", label="Proposed storage"),
            DiagramEdge(source="selected", target="future", label="Proposed queue"),
        ],
    )
    project.architectures = [
        ArchitectureRevision(
            number=4,
            summary="Proposal",
            technologies=[dict(area="api", choice="Python", rationale="Existing")],
            diagram=project.source_diagram,
        )
    ]
    app.db.save(project, "test_design_model")
    model = detail(app, project, ["selected", "second"], architecture_revision=4)["semantic"]
    assert model["architecture_revision"] == 4 and model["origin"] == "source"
    architecture = [edge for edge in model["relations"] if edge["kind"] == "architecture"]
    assert len(architecture) == 2
    assert all(
        edge["origin"] == "design" and edge["resolution"] == "architecture" for edge in architecture
    )
    imports = [edge for edge in model["relations"] if edge["kind"] == "import"]
    assert len(imports) == 1 and imports[0]["origin"] == "source"
    assert imports[0]["resolution"] == "selected"
    assert model["boundaries"][0]["origin"] == "design"
    assert {item["name"] for item in model["classes"]} == {"A", "B"}


@pytest.mark.parametrize(
    "source,status",
    [
        ("def module_function(): pass\n", "empty"),
        ("class Invalid !\n", "unsupported"),
        ("\n".join(f"class C{i}: pass" for i in range(25)), "too_large"),
        ("class A:\n" + "\n".join(f"    f{i}: str" for i in range(33)), "too_large"),
    ],
)
def test_nonready_results_do_not_expose_partial_semantic_models(app, repository, source, status):
    project = make_project(app, repository, {"a.py": source})
    result = detail(app, project)
    assert result["status"] == status
    assert "semantic" not in result and "image" not in result


def test_source_change_during_render_cannot_publish_semantic_data(app, repository, monkeypatch):
    project = make_project(app, repository, {"a.py": "class A: pass\n"})

    def changed(source):
        (repository / "a.py").write_text("class Stale: pass\n")
        return b"<svg/>"

    monkeypatch.setattr("evograph.application.uml.render", changed)
    result = detail(app, project)
    assert result["status"] == "unmapped"
    assert "semantic" not in result and "image" not in result


def test_dynamic_base_arguments_are_never_serialized_as_inheritance(app, repository):
    project = make_project(
        app,
        repository,
        {
            "a.py": "class A(factory('BASE_SECRET')):\n    peer: Other\n",
            "b.ts": "class B extends mixin('BASE_SECRET') { peer: Other; }",
        },
    )
    result = detail(app, project)
    assert result["status"] == "ready"
    assert not result["semantic"]["relations"]
    assert not result["semantic"]["boundaries"]
    assert "SECRET" not in json.dumps(result)


def test_cpp_conditional_noexcept_stays_conditional_and_import_ranges_are_inclusive():
    result = parse_classes(
        "model.hpp",
        """#include <utility>
struct Model {
    bool run() noexcept(check());
    int first = 671, second;
};
""",
    )
    members = {item["name"]: item for item in result["classes"][0]["member_declarations"]}
    assert members["run"]["text"] == "run() : bool noexcept(…)"
    assert members["first"]["text"] == "first : int = …"
    assert members["second"]["text"] == "second : int"
    assert result["import_declarations"] == [{"name": "utility", "line": 1, "end_line": 1}]
    assert "check()" not in json.dumps(result) and "671" not in json.dumps(result)


def test_typescript_canonical_generics_destructuring_optional_and_generator_members(
    app, repository
):
    project = make_project(
        app,
        repository,
        {
            "store.ts": """@decorate('DECORATOR_SECRET')
abstract class Store<T extends {id: string} = DEFAULT_SECRET> extends mixin('BASE_SECRET') {
    map<U extends {id: string} = DEFAULT_SECRET>(value: U): U { return value; }
    read({key, flag = make('NESTED_SECRET'), nested: {value = 827}, ...rest}: Input = outer('OUTER_SECRET')): void {}
    tuple([first, , {key: renamed = 'ARRAY_SECRET'}, ...rest]: Input): void {}
    run?(): void;
    protected async *entries(): AsyncGenerator<string> { yield 'BODY_SECRET'; }
    readonly value!: string;
    handler: <V extends {id: string} = HANDLER_SECRET>(value: V) => V;
    [computed('COMPUTED_SECRET')](): void {}
}
"""
        },
    )
    result = detail(app, project)
    assert result["status"] == "ready"
    owner = result["semantic"]["classes"][0]
    assert owner["name"] == "Store"
    assert owner["declaration"] == "abstract class Store<T extends {id: string} = …> extends …"
    members = {member["name"]: member for member in owner["members"]}
    assert members["map"]["text"] == "map<U extends {id: string} = …>(value : U) : U"
    assert members["read"]["text"] == (
        "read({key, flag = …, nested: {value = …}, ...rest} : Input = …) : void"
    )
    assert members["tuple"]["text"] == (
        "tuple([first, , {key: renamed = …}, ...rest] : Input) : void"
    )
    assert members["run"]["text"] == "run?() : void"
    assert members["entries"]["text"] == "async *entries() : AsyncGenerator<string>"
    assert members["entries"]["visibility"] == "protected"
    assert members["value"]["text"] == "readonly value! : string"
    assert members["handler"]["text"] == "handler : <V extends {id: string} = …>(value: V) => V"
    assert members["[computed]"]["text"] == "[computed]() : void"
    assert not result["semantic"]["relations"]
    assert "SECRET" not in json.dumps(result)
    assert "827" not in " ".join(member["text"] for member in owner["members"])


def test_typescript_original_generic_and_object_type_signature_is_complete():
    parsed = parse_classes(
        "store.ts",
        "class Store { map<T extends {id: string}>(value: T): T { return value; } "
        'read({key, flag = "MASKME"}: {key: string, flag?: string}): void {} }',
    )
    assert [member["text"] for member in parsed["classes"][0]["member_declarations"]] == [
        "map<T extends {id: string}>(value : T) : T",
        "read({key, flag = …} : {key: string, flag?: string}) : void",
    ]
    assert "MASKME" not in json.dumps(parsed)


def test_cpp_canonical_templates_method_clauses_and_qualifier_structure(app, repository):
    project = make_project(
        app,
        repository,
        {
            "store.hpp": """template<typename C = CLASS_SECRET, int N = 837> struct Store final {
    template<typename T = TYPE_SECRET, int M = 729> T map(T value = [] { return "LAMBDA_SECRET"; }());
    virtual void run() const & noexcept(check("NOEXCEPT_SECRET")) override final = 0;
    Store(const Store&) = delete;
    Store() = default;
    Store(int value) : hidden("INITIALIZER_SECRET") {}
    explicit(check("EXPLICIT_SECRET")) operator bool() const;
    constexpr auto array(int (&values)[628]) && -> decltype("DECLTYPE_SECRET");
    const int * const& pointer(int (*callback)(int code = 519));
    template<typename... Ts> void pack(Ts&&... values);
    template<typename T> requires Valid<T> void constrained(T value) requires Other<T>;
    friend void observe();
    mutable const int* volatile field = nullptr;
    int dimensions[418];
};
"""
        },
    )
    result = detail(app, project)
    assert result["status"] == "ready"
    owner = result["semantic"]["classes"][0]
    assert owner["name"] == "Store"
    assert owner["declaration"] == "template<typename C = …, int N = …> struct Store final"
    texts = {member["text"] for member in owner["members"]}
    assert {
        "template<typename T = …, int M = …> map(T value = …) : T",
        "virtual run() : void const & noexcept(…) override final = 0",
        "Store(const Store&) = delete",
        "Store() = default",
        "Store(int value)",
        "explicit(…) operator bool() const",
        "constexpr array(int (&values)[…]) : decltype(…) &&",
        "pointer(int (*callback)(int code = …)) : const int* const&",
        "template<typename... Ts> pack(Ts&&... values) : void",
        "template<typename T> requires Valid<T> constrained(T value) : void requires Other<T>",
        "friend observe() : void",
        "mutable field : const int* volatile = …",
        "dimensions : int[…]",
    } == texts
    assert "SECRET" not in json.dumps(result)
    displayed = owner["declaration"] + " ".join(texts)
    assert not any(value in displayed for value in ["837", "729", "628", "519", "418"])


def test_cpp_nested_template_defaults_and_attribute_arguments_are_masked():
    parsed = parse_classes(
        "store.hpp",
        """template<template<typename T = INNER_SECRET> class Box = OUTER_SECRET>
class [[tag("CLASS_ATTRIBUTE_SECRET")]] Store {
public:
    [[nodiscard("METHOD_ATTRIBUTE_SECRET")]] void run() noexcept;
    void (*callback)(int value = 912);
};""",
    )
    owner = parsed["classes"][0]
    assert owner["declaration"] == (
        "template<template<typename T = …> class Box = …> class [[tag(…)]] Store"
    )
    assert [member["text"] for member in owner["member_declarations"]] == [
        "[[nodiscard(…)]] run() : void noexcept",
        "callback : void (*)(int value = …)",
    ]
    assert "SECRET" not in json.dumps(parsed) and "912" not in json.dumps(parsed)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax requires Python 3.12")
def test_python_canonical_type_parameters_keep_bounds_constraints_and_self(app, repository):
    project = make_project(
        app,
        repository,
        {
            "store.py": """@decorate('DECORATOR_SECRET')
class Store[T: (str, bytes)](factory('BASE_SECRET'), metaclass=factory('META_SECRET')):
    def map[U: str, *Ts, **P](self, value: U = 'DEFAULT_SECRET') -> U:
        return value
    def identity[V](self, value: V) -> V: return value
"""
        },
    )
    result = detail(app, project)
    assert result["status"] == "ready"
    owner = result["semantic"]["classes"][0]
    assert owner["name"] == "Store"
    assert owner["declaration"] == "class Store[T: (str, bytes)](…, metaclass=…)"
    assert [member["text"] for member in owner["members"]] == [
        "map[U: str, *Ts, **P](self, value: U = …) : U",
        "identity[V](self, value: V) : V",
    ]
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize(
    "body,expected,line",
    [
        (
            "    _items: dict[str, InventoryItem]\n"
            "    def __init__(self):\n"
            "        self._items = {'VALUE_SECRET': None}\n",
            "_items : dict[str, InventoryItem]",
            2,
        ),
        (
            "    def __init__(self):\n"
            "        self._items: dict[str, InventoryItem] = {'VALUE_SECRET': None}\n"
            "    def reset(self):\n"
            "        self._items = {}\n",
            "_items : dict[str, InventoryItem] = …",
            3,
        ),
        (
            "    _items: dict[str, InventoryItem]\n"
            "    def __init__(self):\n"
            "        self._items = {}\n"
            "    def reset(self):\n"
            "        self._items: list[InventoryItem] = []\n"
            "        self._items = make('VALUE_SECRET')\n",
            "_items : list[InventoryItem] = …",
            6,
        ),
    ],
)
def test_python_explicit_field_annotations_keep_their_actual_declaration_location(
    app,
    repository,
    body,
    expected,
    line,
):
    source = "class Store:\n" + body
    project = make_project(app, repository, {"store.py": source})
    result = detail(app, project)
    assert result["status"] == "ready"
    fields = [
        member
        for member in result["semantic"]["classes"][0]["members"]
        if member["kind"] == "field"
    ]
    assert len(fields) == 1
    assert fields[0]["text"] == expected
    assert fields[0]["location"] == {"path": "store.py", "line": line, "end_line": line}
    parsed = class_detail.parse_python("store.py", source)["classes"][0]
    assert parsed["members"][0] == "- " + expected.removesuffix(" = …")
    assert "VALUE_SECRET" not in json.dumps(result)


@pytest.mark.parametrize(
    "name,source",
    [
        (
            "store.ts",
            "class Store<T extends {" + ";".join(f"field{i}: string" for i in range(40)) + "}> {}",
        ),
        (
            "store.hpp",
            "template<" + ", ".join(f"typename T{i}" for i in range(40)) + "> struct Store {};",
        ),
    ],
)
def test_canonical_class_headers_fail_the_existing_budget_instead_of_truncating(
    app,
    repository,
    name,
    source,
):
    project = make_project(app, repository, {name: source})
    result = detail(app, project)
    assert result["status"] == "too_large"
    assert "semantic" not in result


def test_generic_header_changes_preserve_class_ids_and_rebind_changed_member_signatures(
    app, repository
):
    project = make_project(
        app,
        repository,
        {"store.ts": "class Store<T extends Base> { map<U extends First>(value: U): U {} }"},
    )
    before = detail(app, project)["semantic"]["classes"][0]
    (repository / "store.ts").write_text(
        "class Store<T extends Other> { map<U extends Second>(value: U): U {} }"
    )
    after = detail(app, refresh(app, project))["semantic"]["classes"][0]
    assert before["name"] == after["name"] == "Store"
    assert before["id"] == after["id"]
    assert before["members"][0]["id"] != after["members"][0]["id"]
    assert before["declaration"] != after["declaration"]


def test_literal_types_and_executable_annotations_use_explicit_masks_consistently(app, repository):
    project = make_project(
        app,
        repository,
        {
            "store.py": """class PythonStore(Base['BASE_LITERAL_SECRET']):
    kind: Literal['TYPE_LITERAL_SECRET']
    computed: make('ANNOTATION_SECRET')
    def run(self, kind: Literal['PARAMETER_LITERAL_SECRET']) -> make('RETURN_SECRET'): pass
""",
            "store.ts": """class TypeScriptStore extends Base<'BASE_LITERAL_SECRET'> {
    kind: 'TYPE_LITERAL_SECRET' | number;
    run(kind: 'PARAMETER_LITERAL_SECRET'): 'RETURN_SECRET' {}
} """,
            "store.hpp": """struct CppStore : Base<918> {
    std::array<int, 819> kind;
    decltype(make("ANNOTATION_SECRET")) computed;
};""",
        },
    )
    result = detail(app, project)
    assert result["status"] == "ready"
    owners = {owner["name"]: owner for owner in result["semantic"]["classes"]}
    python_members = {member["name"]: member["text"] for member in owners["PythonStore"]["members"]}
    assert python_members["kind"] == "kind : Literal[…]"
    assert python_members["computed"] == "computed : …"
    assert python_members["run"] == "run(self, kind: Literal[…]) : …"
    assert owners["TypeScriptStore"]["members"][0]["text"] == "kind : … | number"
    assert owners["CppStore"]["members"][0]["text"] == "kind : std::array<int, …>"
    assert "SECRET" not in json.dumps(result)
    displayed = " ".join(
        owner["declaration"] + " ".join(member["text"] for member in owner["members"])
        for owner in owners.values()
    )
    assert not any(value in displayed for value in ["918", "819"])
    assert any("字面量" in value for value in result["limitations"])


@pytest.mark.parametrize(
    "base",
    ["make('BASE_SECRET').Base", "namespace['BASE_SECRET'].Base"],
)
def test_dynamic_typescript_member_bases_never_escape_through_relation_names(app, repository, base):
    project = make_project(app, repository, {"derived.ts": f"class Derived extends {base} {{}}"})
    result = detail(app, project)
    assert result["status"] == "ready"
    assert not result["semantic"]["relations"] and not result["semantic"]["boundaries"]
    assert "BASE_SECRET" not in json.dumps(result)
    parsed = parse_classes("derived.ts", f"class Derived extends {base} {{}}")
    assert parsed["classes"][0]["bases"] == []
    assert "BASE_SECRET" not in json.dumps(parsed)


@pytest.mark.parametrize("argument", ["837", '"SPECIALIZATION_SECRET"', "decltype(secret())"])
def test_cpp_value_specializations_fail_without_leaking_or_colliding_names(
    app, repository, argument
):
    project = make_project(
        app,
        repository,
        {"store.hpp": f"template<> struct Store<{argument}> {{}};"},
    )
    result = detail(app, project)
    assert result["status"] == "unsupported"
    assert "semantic" not in result and "image" not in result
    assert argument not in json.dumps(result)


def test_cpp_type_specialization_names_keep_their_lexical_identity():
    parsed = parse_classes(
        "store.hpp", "template<> struct Store<int> {}; template<> struct Store<bool> {};"
    )
    assert [owner["name"] for owner in parsed["classes"]] == ["Store<int>", "Store<bool>"]


@pytest.mark.parametrize("failure", ["missing", "timeout", "startup", "compiler", "not_svg"])
def test_standard_renderer_failures_are_typed_without_weakening_validation(
    tmp_path,
    monkeypatch,
    failure,
):
    jar = tmp_path / "plantuml.jar"
    jar.write_bytes(b"synthetic renderer fixture")
    monkeypatch.setenv("EVOGRAPH_PLANTUML_JAR", str(jar))
    monkeypatch.setattr(
        "evograph.application.uml.shutil.which",
        lambda _: None if failure == "missing" else "/fixture/java",
    )

    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("java", 45)
        if failure == "startup":
            raise OSError("synthetic startup failure")
        return SimpleNamespace(
            returncode=1 if failure == "compiler" else 0,
            stdout=b"not svg",
            stderr=b"DO_NOT_EXPOSE_COMPILER_DIAGNOSTICS",
        )

    monkeypatch.setattr("evograph.application.uml.subprocess.run", run)
    source = f"@startuml\nclass RendererFailure_{failure}\n@enduml"
    size = render.cache_info().currsize
    with pytest.raises(UmlRenderError) as error:
        render(source)
    assert "DO_NOT_EXPOSE" not in str(error.value)
    assert render.cache_info().currsize == size
    with pytest.raises(ValueError) as error:
        render("@startuml\n!include forbidden\n@enduml")
    assert not isinstance(error.value, UmlRenderError)


def test_renderer_failure_returns_unrendered_fresh_semantics_and_raw_source(
    app, repository, monkeypatch
):
    project = make_project(app, repository, {"a.py": "class A: pass\n"})
    before = app.db.get(project.id).model_dump()

    def unavailable(source):
        raise UmlRenderError("Local compiler is unavailable")

    monkeypatch.setattr("evograph.application.uml.render", unavailable)
    result = detail(app, project)
    assert result["status"] == "ready" and result["render_status"] == "unavailable"
    assert result["render_error"] == "Local compiler is unavailable"
    assert "image" not in result
    assert result["semantic"]["classes"][0]["name"] == "A"
    assert result["diagram"]["source"].startswith("@startuml")
    assert app.db.get(project.id).model_dump() == before


def test_source_change_during_failed_render_still_rejects_semantic_data(
    app, repository, monkeypatch
):
    project = make_project(app, repository, {"a.py": "class A: pass\n"})

    def unavailable(source):
        (repository / "a.py").write_text("class Changed: pass\n")
        raise UmlRenderError("Local compiler is unavailable")

    monkeypatch.setattr("evograph.application.uml.render", unavailable)
    result = detail(app, project)
    assert result["status"] == "unmapped"
    assert "semantic" not in result and "diagram" not in result
    assert "image" not in result and "render_status" not in result
    assert not app.db.get(project.id).uml_diagrams


@pytest.mark.parametrize("stage", ["validate_source", "render"])
def test_validation_and_unexpected_errors_never_degrade_to_ready_semantics(
    app, repository, monkeypatch, stage
):
    project = make_project(app, repository, {"a.py": "class A: pass\n"})

    def invalid(source):
        raise ValueError("Unsafe source must remain blocked")

    monkeypatch.setattr(f"evograph.application.uml.{stage}", invalid)
    with pytest.raises(ValueError, match="Unsafe source"):
        detail(app, project)
    assert not app.db.get(project.id).uml_diagrams


def test_failed_compilation_does_not_poison_renderer_cache_or_save_diagram_history(
    app,
    repository,
    monkeypatch,
    tmp_path,
):
    project = make_project(app, repository, {"a.py": "class CompilerRetryableUncached: pass\n"})
    before = app.db.get(project.id).model_dump()
    jar = tmp_path / "plantuml.jar"
    jar.write_bytes(b"synthetic renderer fixture")
    monkeypatch.setenv("EVOGRAPH_PLANTUML_JAR", str(jar))
    monkeypatch.setattr("evograph.application.uml.shutil.which", lambda _: "/fixture/java")
    calls = []

    def compile_once(*args, **kwargs):
        calls.append(kwargs["input"])
        return SimpleNamespace(
            returncode=1 if len(calls) == 1 else 0,
            stdout=b"" if len(calls) == 1 else b"<svg/>",
            stderr=b"transient compiler failure",
        )

    monkeypatch.setattr("evograph.application.uml.subprocess.run", compile_once)
    monkeypatch.setattr("evograph.application.uml.render", render)
    first, second, third = (detail(app, project) for _ in range(3))
    assert first["render_status"] == "unavailable" and "image" not in first
    assert second["render_status"] == third["render_status"] == "ready"
    assert first["semantic"] == second["semantic"] == third["semantic"]
    assert second["image"] == third["image"]
    assert len(calls) == 2
    assert app.db.get(project.id).model_dump() == before
