"""Local syntax extraction for one already-bounded source file.

This module never opens a path, follows imports, runs a compiler or contacts a model.
The caller owns file selection, byte/entity/member budgets, and diagram escaping.
Tree-sitter supplies declaration boundaries; these are not resolved program types.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import PurePosixPath

import tree_sitter_cpp
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

CPP_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ipp", ".tpp"}
TS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}
SUPPORTED_SUFFIXES = CPP_SUFFIXES | TS_SUFFIXES | {".vue"}
_VISIBILITY = {"public": "+", "protected": "#", "private": "-"}
_CPP_LANGUAGE = Language(tree_sitter_cpp.language())
_TS_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
_TSX_LANGUAGE = Language(tree_sitter_typescript.language_tsx())
_CPP_LIMIT = (
    "C/C++ 仅按语法提取，未展开宏、条件编译、include、别名或模板；条件分支可能同时出现。"
    "省略函数内/匿名类及类外方法实现；签名省略默认值、数组长度、模板参数和部分修饰符。"
    "无结果不代表编译后没有类。"
)
_TS_LIMIT = (
    "TS/JS 仅按语法提取，未解析导入、别名、mixin、推断类型或运行时成员；"
    "省略类型别名、函数内/匿名类，JS 使用 TypeScript 语法解析。"
    "签名省略默认值、泛型参数和部分修饰符；计算成员名为占位符，继承仅反映源码写法。"
)


def _text(node: Node | None) -> str:
    return " ".join(node.text.decode("utf-8", errors="replace").split()) if node else ""


def _field(node: Node, name: str) -> Node | None:
    return node.child_by_field_name(name)


def _children(node: Node | None) -> list[Node]:
    return node.named_children if node else []


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _type(node: Node | None) -> str:
    if not node:
        return ""
    if node.type.endswith("_specifier") and _field(node, "body"):
        return _text(_field(node, "name")) or "anonymous"
    if node.type == "type_annotation":
        return _text(node).removeprefix(":").strip()
    # Expression-based C++ types can contain literal values, not just type names.
    if node.type in {"decltype", "decltype_specifier"}:
        return "decltype(…)"
    return _text(node)


def _declarator_child(node: Node) -> Node | None:
    child = _field(node, "declarator")
    if child:
        return child
    if node.type in {"reference_declarator", "parenthesized_declarator"}:
        return next(iter(node.named_children), None)
    return None


def _cpp_declarator(node: Node | None) -> tuple[str, str]:
    """Return the identifier and pointer/reference/array adornments, never initializers."""
    if not node:
        return "", ""
    if node.type in {
        "identifier",
        "field_identifier",
        "type_identifier",
        "destructor_name",
        "operator_name",
        "qualified_identifier",
    }:
        return _text(node), ""
    if node.type == "operator_cast":
        return f"operator {_type(_field(node, 'type'))}", ""
    name, decoration = _cpp_declarator(_declarator_child(node))
    if node.type in {"pointer_declarator", "abstract_pointer_declarator"}:
        qualifiers = " ".join(_text(c) for c in node.named_children if c.type == "type_qualifier")
        decoration += "*" + (" " + qualifiers if qualifiers else "")
    elif node.type in {"reference_declarator", "abstract_reference_declarator"}:
        decoration += "&&" if any(c.type == "&&" for c in node.children) else "&"
    elif node.type in {"array_declarator", "abstract_array_declarator"}:
        decoration += "[]"
    return name, decoration


def _cpp_declared_type(node: Node) -> str:
    qualifiers = " ".join(_text(c) for c in node.named_children if c.type == "type_qualifier")
    return " ".join(filter(None, (qualifiers, _type(_field(node, "type")))))


def _cpp_parameters(node: Node | None) -> str:
    result = []
    for parameter in _children(node):
        if parameter.type in {"parameter_declaration", "optional_parameter_declaration"}:
            name, decoration = _cpp_declarator(_field(parameter, "declarator"))
            label = (_cpp_declared_type(parameter) + decoration).strip()
            result.append(" ".join(filter(None, (label, name))))
        elif parameter.type in {"variadic_parameter_declaration", "variadic_parameter"}:
            result.append("…")
    if node and any(child.type == "..." for child in node.children):
        result.append("…")
    return ", ".join(result)


def _cpp_member(node: Node, visibility: str) -> list[str]:
    if node.type == "template_declaration":
        return [
            m
            for c in node.named_children
            if c.type != "template_parameter_list"
            for m in _cpp_member(c, visibility)
        ]
    if node.type not in {"field_declaration", "declaration", "function_definition"}:
        return []
    result = []
    declared_type = _cpp_declared_type(node)
    for declaration in node.children_by_field_name("declarator"):
        current = declaration
        return_decoration = ""
        while current.type not in {"function_declarator", "operator_cast"}:
            following = _declarator_child(current)
            if not following:
                break
            if current.type == "pointer_declarator":
                return_decoration += "*"
            elif current.type == "reference_declarator":
                return_decoration += "&"
            current = following
        name, decoration = _cpp_declarator(declaration)
        if not name:
            continue
        if current.type == "operator_cast":
            function = _field(current, "declarator")
            return_type = ""
        elif current.type == "function_declarator":
            function = current
            return_type = declared_type + return_decoration
        else:
            function = None
            return_type = ""
        if function:
            params = _cpp_parameters(_field(function, "parameters"))
            inner = _field(function, "declarator")
            if inner and inner.type == "parenthesized_declarator":
                # A function pointer field is a field, not a method.
                signature = f"{name} : {declared_type} ({decoration})({params})"
            else:
                trailing = next(
                    (c for c in function.named_children if c.type == "trailing_return_type"), None
                )
                if trailing:
                    return_type = " ".join(_type(c) for c in trailing.named_children)
                modifiers = " ".join(
                    _text(c)
                    for c in function.named_children
                    if c.type in {"type_qualifier", "virtual_specifier"}
                )
                signature = f"{name}({params})"
                if return_type:
                    signature += f" : {return_type}"
                if modifiers:
                    signature += " " + modifiers
        else:
            signature = f"{name} : {declared_type}{decoration}".rstrip(" :")
        result.append(f"{_VISIBILITY[visibility]} {signature}")
    return result


def _cpp_class(node: Node, scope: tuple[str, ...], line_offset: int) -> dict | None:
    name, body = _field(node, "name"), _field(node, "body")
    if not name or not body:
        return None
    kind = "struct" if node.type == "struct_specifier" else "class"
    visibility = "public" if kind == "struct" else "private"
    members = []
    # Conditional sections contain declaration nodes; maintain visibility in source order.
    pending = list(reversed(body.named_children))
    while pending:
        child = pending.pop()
        if child.type == "access_specifier":
            visibility = _text(child)
        elif child.type.startswith("preproc_") and child.type not in {
            "preproc_def",
            "preproc_function_def",
        }:
            pending.extend(reversed(child.named_children))
        else:
            members.extend(_cpp_member(child, visibility))
    bases = []
    for clause in node.named_children:
        if clause.type == "base_class_clause":
            bases.extend(
                _type(c)
                for c in clause.named_children
                if c.type != "access_specifier" and c.type != "attribute_declaration"
            )
    return {
        "name": "::".join((*scope, _text(name))),
        "kind": kind,
        "line": node.start_point.row + line_offset + 1,
        "members": members,
        "bases": bases,
    }


def _parse_cpp(text: str) -> dict:
    root = Parser(_CPP_LANGUAGE).parse(text.encode("utf-8")).root_node
    if root.has_error:
        raise ValueError(
            "C/C++ 声明不完整或宏语法暂不支持；请改选普通声明文件或查看明确标记的局部设计图"
        )
    classes, imports = [], []
    limitations = [_CPP_LIMIT]
    pending = [(root, ())]
    containers = {
        "translation_unit",
        "declaration_list",
        "field_declaration_list",
        "template_declaration",
        "declaration",
        "field_declaration",
        "linkage_specification",
    }
    while pending:
        node, scope = pending.pop()
        if node.type == "preproc_include":
            path = _field(node, "path")
            if path and path.type in {"string_literal", "system_lib_string"}:
                imports.append(_text(path)[1:-1])
            else:
                limitations.append("已省略宏定义的 include 目标，未运行预处理器。")
        elif node.type == "namespace_definition":
            name = _text(_field(node, "name")) or f"(anonymous@{node.start_point.row + 1})"
            body = _field(node, "body")
            if body:
                pending.append((body, (*scope, name)))
        elif node.type in {"class_specifier", "struct_specifier"}:
            extracted = _cpp_class(node, scope, 0)
            if extracted:
                classes.append(extracted)
                pending.append((_field(node, "body"), (*scope, _text(_field(node, "name")))))
        elif node.type in containers or node.type in {
            "preproc_if",
            "preproc_ifdef",
            "preproc_else",
            "preproc_elif",
        }:
            pending.extend((c, scope) for c in reversed(node.named_children))
    return {"classes": classes, "imports": _unique(imports), "limitations": _unique(limitations)}


def _ts_visibility(node: Node) -> str:
    modifier = next((c for c in node.named_children if c.type == "accessibility_modifier"), None)
    if _text(_field(node, "name")).startswith("#"):
        return "-"
    return _VISIBILITY.get(_text(modifier), "+")


def _ts_name(node: Node | None) -> str:
    if not node:
        return ""
    if node.type == "computed_property_name":
        return "[computed]"
    if node.type in {"object_pattern", "array_pattern"}:
        return "{…}" if node.type == "object_pattern" else "[…]"
    if node.type == "rest_pattern":
        return "..." + _ts_name(next(iter(node.named_children), None))
    if node.type in {"assignment_pattern", "object_assignment_pattern"}:
        return _ts_name(_field(node, "left"))
    return _text(node)


def _ts_parameter(node: Node) -> str:
    if node.type not in {"required_parameter", "optional_parameter"}:
        return _ts_name(node)
    name = _ts_name(_field(node, "pattern"))
    if node.type == "optional_parameter":
        name += "?"
    annotation = _type(_field(node, "type"))
    return name + (f" : {annotation}" if annotation else "")


def _ts_members(body: Node | None) -> list[str]:
    members = []
    for member in _children(body):
        visibility = _ts_visibility(member)
        name = _ts_name(_field(member, "name"))
        annotation = _type(_field(member, "type"))
        if member.type in {"public_field_definition", "property_signature"}:
            optional = "?" if any(c.type == "?" for c in member.children) else ""
            members.append(
                f"{visibility} {name}{optional}" + (f" : {annotation}" if annotation else "")
            )
        elif member.type in {
            "method_definition",
            "method_signature",
            "abstract_method_signature",
            "construct_signature",
            "call_signature",
        }:
            parameters = _field(member, "parameters")
            params = ", ".join(
                _ts_parameter(c) for c in _children(parameters) if c.type != "comment"
            )
            if member.type == "construct_signature":
                name = "new"
            elif member.type == "call_signature":
                name = "(call)"
            accessor = next((c.type for c in member.children if c.type in {"get", "set"}), "")
            if accessor:
                name = accessor + " " + name
            returns = _type(_field(member, "return_type")) or annotation
            members.append(f"{visibility} {name}({params})" + (f" : {returns}" if returns else ""))
            if name == "constructor":
                # Parameter properties really declare fields, but their initializer values do not.
                for parameter in _children(parameters):
                    if any(
                        c.type in {"accessibility_modifier", "readonly"} for c in parameter.children
                    ):
                        members.append(f"{_ts_visibility(parameter)} {_ts_parameter(parameter)}")
        elif member.type == "index_signature":
            index_type = _type(_field(member, "index_type"))
            members.append(
                f"+ [{name} : {index_type}]" + (f" : {annotation}" if annotation else "")
            )
    return members


def _ts_bases(node: Node) -> list[str]:
    bases = []
    for child in node.named_children:
        if child.type == "extends_type_clause":
            bases.extend(_type(c) for c in child.named_children)
        elif child.type == "class_heritage":
            for clause in child.named_children:
                if clause.type == "extends_clause":
                    value = _field(clause, "value")
                    # Do not render a mixin call and its arbitrary arguments as a base type.
                    if value and value.type in {
                        "identifier",
                        "member_expression",
                        "nested_identifier",
                    }:
                        bases.append(_text(value) + _text(_field(clause, "type_arguments")))
                elif clause.type == "implements_clause":
                    bases.extend(_type(c) for c in clause.named_children)
    return bases


def _parse_ts(text: str, *, tsx: bool = False, line_offset: int = 0) -> dict:
    root = Parser(_TSX_LANGUAGE if tsx else _TS_LANGUAGE).parse(text.encode("utf-8")).root_node
    if root.has_error:
        raise ValueError("TS/JS 声明无法可靠解析；请修复语法或缩小选择范围")
    classes, imports = [], []
    pending = [(root, ())]
    containers = {
        "program",
        "statement_block",
        "export_statement",
        "ambient_declaration",
        "expression_statement",
        "lexical_declaration",
        "variable_declaration",
        "variable_declarator",
    }
    while pending:
        node, scope = pending.pop()
        if node.type in {"import_statement", "export_statement", "import_require_clause"}:
            source = _field(node, "source")
            if source and source.type == "string":
                imports.append(_text(source)[1:-1])
            if node.type == "import_statement":
                pending.extend(
                    (c, scope)
                    for c in reversed(node.named_children)
                    if c.type == "import_require_clause"
                )
        if node.type in {"internal_module", "module"}:
            name = _text(_field(node, "name"))
            body = _field(node, "body")
            if body:
                pending.append((body, (*scope, name)))
        elif node.type in {
            "class_declaration",
            "abstract_class_declaration",
            "class",
            "interface_declaration",
        }:
            name = _field(node, "name")
            if name:
                classes.append(
                    {
                        "name": ".".join((*scope, _text(name))),
                        "kind": "interface" if node.type == "interface_declaration" else "class",
                        "line": node.start_point.row + line_offset + 1,
                        "members": _ts_members(_field(node, "body")),
                        "bases": _ts_bases(node),
                    }
                )
        elif node.type in containers:
            pending.extend((c, scope) for c in reversed(node.named_children))
    return {"classes": classes, "imports": _unique(imports), "limitations": [_TS_LIMIT]}


class _VueScripts(HTMLParser):
    """Read only top-level SFC script blocks with the standard HTML tokenizer."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.scripts: list[tuple[dict[str, str | None], str, int]] = []
        self.active: tuple[dict[str, str | None], list[str], int] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and not self.stack:
            raw_start = self.get_starttag_text() or ""
            self.active = (dict(attrs), [], self.getpos()[0] - 1 + raw_start.count("\n"))
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.active:
            attrs, pieces, offset = self.active
            self.scripts.append((attrs, "".join(pieces), offset))
            self.active = None
        if tag in self.stack:
            self.stack = self.stack[: len(self.stack) - self.stack[::-1].index(tag) - 1]

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and not self.stack:
            self.scripts.append((dict(attrs), "", self.getpos()[0] - 1))

    def handle_data(self, data: str) -> None:
        if self.active:
            self.active[1].append(data)


def _parse_vue(text: str) -> dict:
    parser = _VueScripts()
    parser.feed(text)
    parser.close()
    if parser.active:
        raise ValueError("Vue script 块未闭合")
    result = {
        "classes": [],
        "imports": [],
        "limitations": [
            _TS_LIMIT,
            "Vue 仅提取顶层 script 声明；模板、样式、Composition API 状态和 "
            "defineProps/defineEmits 宏不属于类。script setup 组件可以没有类声明。",
        ],
    }
    for attrs, script, offset in parser.scripts:
        language = (attrs.get("lang") or "js").lower()
        if language not in {"ts", "typescript", "tsx", "js", "javascript", "jsx"}:
            raise ValueError(f"Vue script 语言 {language!r} 暂不支持；不会生成不完整类图")
        if attrs.get("src"):
            result["imports"].append(attrs["src"])
            result["limitations"].append("Vue script src 仅作为边界依赖，未读取外部脚本。")
            continue
        parsed = _parse_ts(script, tsx=language in {"tsx", "jsx"}, line_offset=offset)
        result["classes"].extend(parsed["classes"])
        result["imports"].extend(parsed["imports"])
    result["imports"] = _unique(result["imports"])
    result["limitations"] = _unique(result["limitations"])
    return result


def parse_classes(path: str, text: str) -> dict:
    """Extract named declarations from text; path is a language hint, never opened.

    The result contains classes (name, kind, 1-based line, members, bases), static
    import labels, and explicit language limitations. Invalid syntax is rejected
    rather than presenting a recovered parse as verified class coverage.
    """
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in CPP_SUFFIXES:
        return _parse_cpp(text)
    if suffix in TS_SUFFIXES:
        return _parse_ts(text, tsx=suffix in {".tsx", ".jsx"})
    if suffix == ".vue":
        return _parse_vue(text)
    raise ValueError(f"暂不支持此类提取语言：{suffix or '(无后缀)'}")
