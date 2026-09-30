"""Public synthetic sources only: parser evidence must not become runtime claims."""

import pytest
from evograph.application.class_extractors import SUPPORTED_SUFFIXES, parse_classes


def by_name(result):
    return {item["name"]: item for item in result["classes"]}


def test_cpp_classes_structs_namespaces_members_and_static_include_labels():
    result = parse_classes(
        "selected/model.hpp",
        """#include <vector>
#include "boundary/base.hpp"
namespace Alpha::Models {
class Item : public external::Base, protected Interface {
public:
    Item(int id = 123) : id(id) {}
    virtual bool run(const Input& input, int retries = 8) const { return true; }
    auto label() -> std::string;
    class Nested { double weight = 9.5; };
protected:
    std::vector<Entry> entries;
private:
    int id = 99, *pointer = nullptr;
    const char *token = "DO_NOT_EMIT_FIELD_DEFAULT";
};
}
namespace Beta { struct Item { int value; }; }
""",
    )
    classes = by_name(result)
    assert list(classes) == ["Alpha::Models::Item", "Alpha::Models::Item::Nested", "Beta::Item"]
    item = classes["Alpha::Models::Item"]
    assert item["kind"] == "class" and item["line"] == 4
    assert item["bases"] == ["external::Base", "Interface"]
    assert "+ Item(int id)" in item["members"]
    assert "+ run(const Input& input, int retries) : bool const" in item["members"]
    assert "+ label() : std::string" in item["members"]
    assert "# entries : std::vector<Entry>" in item["members"]
    assert "- id : int" in item["members"]
    assert "- pointer : int*" in item["members"]
    assert classes["Beta::Item"]["members"] == ["+ value : int"]
    assert "DO_NOT_EMIT" not in str(result) and "123" not in str(result)
    assert result["imports"] == ["vector", "boundary/base.hpp"]
    assert "未展开宏" in " ".join(result["limitations"])


def test_cpp_function_pointers_arrays_operators_and_template_members():
    result = parse_classes(
        "selected/item.cpp",
        """struct Item {
    void (*callback)(int code);
    int values[40];
    const Thing *find(const char *key = "DO_NOT_EMIT");
    ~Item();
    operator bool() const;
    template <typename T> T convert(T value = T{});
};""",
    )
    members = result["classes"][0]["members"]
    assert "+ callback : void (*)(int code)" in members
    assert "+ values : int[]" in members
    assert "+ find(const char* key) : const Thing*" in members
    assert "+ ~Item()" in members
    assert "+ operator bool() const" in members
    assert "+ convert(T value) : T" in members
    assert "DO_NOT_EMIT" not in str(result)


def test_cpp_does_not_extract_comments_strings_or_function_local_classes():
    result = parse_classes(
        "selected/item.h",
        """// class Pretend {};
const char *text = "struct AlsoPretend {};";
void outside() { class Local {}; }
namespace Actual { struct Visible { void f() { class Local {}; } }; }
""",
    )
    assert list(by_name(result)) == ["Actual::Visible"]


def test_cpp_conditional_macros_are_limited_not_semantic_claims():
    result = parse_classes(
        "selected/item.hpp",
        """#include HEADER_MACRO
#if FEATURE
struct Enabled {};
#else
struct Disabled {};
#endif
""",
    )
    assert list(by_name(result)) == ["Enabled", "Disabled"]
    assert result["imports"] == []
    assert "宏定义的 include" in " ".join(result["limitations"])
    empty = parse_classes("selected/generated.hpp", "DECLARE_MODEL(Account);\n")
    assert empty["classes"] == []
    assert "不代表编译后没有类" in " ".join(empty["limitations"])


def test_typescript_interface_class_namespaces_inheritance_and_members():
    result = parse_classes(
        "selected/model.ts",
        """import type { Outside as X } from "outside";
export { Elsewhere } from "./boundary";
namespace App.Models {
export interface Item<T> extends Base<T>, Shared.Other {
    readonly id?: string;
    run(input: Input, optional?: string): Promise<T>;
    new(value: number): T;
    [key: string]: string;
}
export abstract class Model<T> extends Parent<T> implements Item<T>, Other {
    private secret: string = "DO_NOT_EMIT_FIELD_DEFAULT";
    protected optional?: number;
    constructor(public name: string, private count = 23) {}
    async run(input: Input, fallback = "DO_NOT_EMIT_PARAM_DEFAULT"): Promise<T> { return ignored(); }
    get id(): string { return this.secret; }
    set id(value: string) {}
    handler: (value: X) => void;
}
}
namespace Other { export class Model {} }
""",
    )
    classes = by_name(result)
    assert list(classes) == ["App.Models.Item", "App.Models.Model", "Other.Model"]
    interface = classes["App.Models.Item"]
    assert interface["kind"] == "interface" and interface["line"] == 4
    assert interface["bases"] == ["Base<T>", "Shared.Other"]
    assert "+ id? : string" in interface["members"]
    assert "+ run(input : Input, optional? : string) : Promise<T>" in interface["members"]
    assert "+ new(value : number) : T" in interface["members"]
    assert "+ [key : string] : string" in interface["members"]
    model = classes["App.Models.Model"]
    assert model["bases"] == ["Parent<T>", "Item<T>", "Other"]
    assert "- secret : string" in model["members"]
    assert "# optional? : number" in model["members"]
    assert "+ constructor(name : string, count)" in model["members"]
    assert "+ name : string" in model["members"] and "- count" in model["members"]
    assert "+ get id() : string" in model["members"]
    assert "+ set id(value : string)" in model["members"]
    assert "DO_NOT_EMIT" not in str(result) and "23" not in str(result)
    assert result["imports"] == ["outside", "./boundary"]


def test_typescript_ignores_local_classes_values_and_dynamic_mixin_arguments():
    result = parse_classes(
        "selected/model.ts",
        """import OldStyle = require("legacy");
// class Pretend {}
const text = "class AlsoPretend {}";
function body() { class Local {} }
type Alias = { field: string };
class Actual extends mixin("DO_NOT_EMIT_BASE_ARGUMENT") {
    #privateField = "DO_NOT_EMIT_FIELD";
    run({ id, token = "DO_NOT_EMIT_DESTRUCTURED" }: Input, ...rest: string[]) {}
}
""",
    )
    assert list(by_name(result)) == ["Actual"]
    assert result["classes"][0]["bases"] == []
    assert "- #privateField" in result["classes"][0]["members"]
    assert "+ run({…} : Input, ...rest : string[])" in result["classes"][0]["members"]
    assert result["imports"] == ["legacy"]
    assert "DO_NOT_EMIT" not in str(result)


@pytest.mark.parametrize("suffix", [".tsx", ".jsx"])
def test_tsx_and_jsx_skip_component_function_bodies(suffix):
    result = parse_classes(
        "selected/view" + suffix,
        """import React from "react";
class Model { render() { return <div>class NotAClass</div>; } }
const View = () => <span/>;
""",
    )
    assert list(by_name(result)) == ["Model"]
    assert result["imports"] == ["react"]


def test_vue_parses_only_top_level_scripts_and_keeps_source_lines():
    result = parse_classes(
        "selected/view.vue",
        """<template>
  <div>class Fake {}</div>
  <script>class InTemplate {}</script>
</template>
<script
 lang="ts">
import { thing } from "./boundary";
interface Props { id: string }
class Model { count = 10; }
</script>
<script setup lang="ts">
const props = defineProps<Props>();
interface SetupInput { enabled: boolean }
</script>
<style>.class { color: red }</style>
""",
    )
    classes = by_name(result)
    assert list(classes) == ["Props", "Model", "SetupInput"]
    assert classes["Props"]["line"] == 8
    assert classes["Model"]["line"] == 9
    assert classes["SetupInput"]["line"] == 13
    assert result["imports"] == ["./boundary"]
    assert "Composition API" in " ".join(result["limitations"])


def test_vue_setup_without_classes_and_external_scripts_are_honest_boundaries():
    setup = parse_classes(
        "selected/view.vue",
        """<script setup lang="ts">
import { ref } from "vue";
const count = ref(0);
const props = defineProps<{ title: string }>();
</script>""",
    )
    assert setup["classes"] == [] and setup["imports"] == ["vue"]
    assert "可以没有类声明" in " ".join(setup["limitations"])
    external = parse_classes("selected/external.vue", '<script src="./external.ts"/>')
    assert external["classes"] == [] and external["imports"] == ["./external.ts"]
    assert "未读取外部脚本" in " ".join(external["limitations"])
    with pytest.raises(ValueError, match="coffee.*暂不支持"):
        parse_classes("selected/other.vue", '<script lang="coffee">class Model</script>')


@pytest.mark.parametrize(
    "path,source",
    [
        ("model.cpp", "class Model { int"),
        ("model.ts", "class Model { member: "),
        ("model.vue", '<script lang="ts">class Model {</script>'),
        ("model.vue", '<script lang="ts">class Model {}'),
        ("model.unknown", "class Model {}"),
    ],
)
def test_malformed_and_unsupported_sources_fail_closed(path, source):
    with pytest.raises(ValueError):
        parse_classes(path, source)


@pytest.mark.parametrize("suffix", sorted(SUPPORTED_SUFFIXES))
def test_empty_supported_file_is_not_an_error(suffix):
    result = parse_classes("no/filesystem/path/exists" + suffix, "")
    assert result["classes"] == [] and result["imports"] == []
    assert result["limitations"]


def test_parser_cannot_open_or_follow_any_file(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("parser must only use provided text")

    monkeypatch.setattr("builtins.open", denied)
    result = parse_classes(
        "../../outside/unread.ts", 'import { X } from "../../outside"; class A {}'
    )
    assert result["classes"][0]["name"] == "A"
    assert result["imports"] == ["../../outside"]


@pytest.mark.parametrize(
    "source",
    [
        """#include "CoreMinimal.h"
#include "Character.generated.h"
UCLASS(Blueprintable)
class MYGAME_API ACharacter : public AActor {
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere)
    float Speed = 100.f;
    UFUNCTION(BlueprintCallable)
    void Move(float Delta);
};""",
        """USTRUCT(BlueprintType)
struct FItem {
    GENERATED_BODY()
    UPROPERTY(EditAnywhere)
    int32 Count;
};""",
    ],
)
def test_unreal_reflection_and_api_macros_fail_with_explicit_limitation(source):
    # No Unreal compiler/UHT runs; an empty model here would be a misleading result.
    with pytest.raises(ValueError, match="宏语法暂不支持"):
        parse_classes("selected/UnrealModel.h", source)


def test_vue_default_javascript_script_parses_named_classes():
    result = parse_classes(
        "selected/view.vue", '<script>export class Model { value = "DO_NOT_EMIT"; }</script>'
    )
    assert list(by_name(result)) == ["Model"]
    assert result["classes"][0]["line"] == 1
    assert result["classes"][0]["members"] == ["+ value"]
    assert "DO_NOT_EMIT" not in str(result)


@pytest.mark.parametrize(
    "unsupported",
    [
        '<script lang="coffee">class Model</script>',
        '<script lang="coffee" src="./model.coffee"/>',
    ],
)
def test_mixed_vue_scripts_never_report_partial_supported_class_coverage(unsupported):
    source = '<script lang="ts">export class Supported {}</script>' + unsupported
    with pytest.raises(ValueError, match="coffee.*暂不支持"):
        parse_classes("selected/mixed.vue", source)
