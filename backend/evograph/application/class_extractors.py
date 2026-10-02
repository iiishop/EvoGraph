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
    "省略函数内/匿名类及类外方法实现；声明保留模板和修饰符，默认值、表达式及数组长度使用占位符。"
    "兼容 UML 行可能简写；规范声明不包含方法体。"
    "无结果不代表编译后没有类。"
)
_TS_LIMIT = (
    "TS/JS 仅按语法提取，未解析导入、别名、mixin、推断类型或运行时成员；"
    "省略类型别名、函数内/匿名类，JS 使用 TypeScript 语法解析。"
    "声明保留泛型与解构，默认值和表达式使用占位符；兼容 UML 行可能简写。"
    "计算成员名为占位符，动态基类表达式不解析，继承仅反映源码写法。"
)


def _text(node: Node | None) -> str:
    return " ".join(node.text.decode("utf-8", errors="replace").split()) if node else ""


def _field(node: Node, name: str) -> Node | None:
    return node.child_by_field_name(name)


def _children(node: Node | None) -> list[Node]:
    return node.named_children if node else []


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _syntax(node: Node | None, replacements: dict[int, str] | None = None) -> str:
    """Copy declaration tokens only, masking syntax-owned expression/default spans.

    Tree-sitter, rather than text matching, determines all replacement boundaries.
    Recursing into types matters: decltype/defaults may be nested in a pointer,
    template, callback signature or destructuring binding.
    """
    if not node:
        return ""
    replacements = replacements or {}
    if node.id in replacements:
        return replacements[node.id]
    if node.type in {"comment", "decorator"}:
        return ""
    if node.type == "computed_property_name":
        return "[computed]"
    if node.type in {"decltype", "decltype_specifier"}:
        return "decltype(…)"
    if node.type in {"noexcept", "explicit_function_specifier", "alignas_qualifier"}:
        label = {"explicit_function_specifier": "explicit", "alignas_qualifier": "alignas"}.get(
            node.type, node.type
        )
        return label + ("(…)" if any(c.type == "(" for c in node.children) else "")
    if node.type == "default_type":  # TypeScript's wrapper includes the equals token.
        return "= …"
    if node.is_named and node.type in {
        "string",
        "string_literal",
        "raw_string_literal",
        "char_literal",
        "number",
        "number_literal",
        "true",
        "false",
        "null",
        "null_literal",
        "template_string",
        "call_expression",
        "new_expression",
        "lambda_expression",
        "arrow_function",
        "statement_block",
        "compound_statement",
        "requires_expression",
    }:
        return "…"
    local = dict(replacements)
    fields = []
    if node.type in {"assignment_pattern", "object_assignment_pattern"}:
        fields.append("right")
    if node.type in {"required_parameter", "optional_parameter", "type_parameter"}:
        fields.append("value")
    if node.type in {"optional_parameter_declaration", "optional_type_parameter_declaration"}:
        fields.extend(("default_value", "default_type"))
    if node.type == "init_declarator":
        fields.append("value")
    for field in fields:
        value = _field(node, field)
        if value and value.type != "default_type":
            local[value.id] = "…"
    if node.type in {"array_declarator", "abstract_array_declarator"}:
        size = _field(node, "size")
        if size:
            local[size.id] = "…"
    if node.type == "attribute":
        for child in node.named_children:
            if child.type == "argument_list":
                local[child.id] = "(…)"
    data, offset, parts = node.text, 0, []
    for child in node.children:
        start, end = child.start_byte - node.start_byte, child.end_byte - node.start_byte
        parts.extend((data[offset:start].decode("utf-8", errors="replace"), _syntax(child, local)))
        offset = end
    parts.append(data[offset:].decode("utf-8", errors="replace"))
    return " ".join("".join(parts).split())


def _type(node: Node | None) -> str:
    if not node:
        return ""
    if node.type.endswith("_specifier") and _field(node, "body"):
        return _syntax(_field(node, "name")) or "anonymous"
    if node.type == "type_annotation":
        return _syntax(node).removeprefix(":").strip()
    # Expression-based C++ types can contain literal values, not just type names.
    if node.type in {"decltype", "decltype_specifier"}:
        return "decltype(…)"
    return _syntax(node)


def _lines(node: Node, line_offset: int = 0) -> dict:
    return {
        "line": node.start_point.row + line_offset + 1,
        "end_line": node.end_point.row
        + line_offset
        + (0 if node.end_point.column == 0 and node.end_point.row > node.start_point.row else 1),
    }


def _declaration(node, name, kind, text, visibility, qualifiers=(), line_offset=0):
    return {
        "name": name,
        "kind": kind,
        "text": text,
        "visibility": visibility,
        "qualifiers": _unique(list(qualifiers)),
        **_lines(node, line_offset),
    }


def _cpp_qualifiers(node: Node, function: Node | None) -> list[str]:
    recognized = {
        "static",
        "virtual",
        "inline",
        "explicit",
        "constexpr",
        "consteval",
        "constinit",
        "const",
        "volatile",
        "mutable",
        "thread_local",
        "override",
        "final",
        "friend",
    }
    children = [*node.children, *(function.children if function else [])]
    return _unique(
        [
            "noexcept"
            if child.type == "noexcept"
            else "explicit"
            if child.type == "explicit_function_specifier"
            else _text(child)
            for child in children
            if child.type in {"noexcept", "explicit_function_specifier"}
            or _text(child) in recognized
        ]
    )


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


def _cpp_parameters(node: Node | None, *, mask_defaults: bool = False) -> str:
    result = []
    for parameter in _children(node):
        if parameter.type in {"parameter_declaration", "optional_parameter_declaration"}:
            if mask_defaults:
                result.append(_syntax(parameter))
                continue
            name, decoration = _cpp_declarator(_field(parameter, "declarator"))
            label = (_cpp_declared_type(parameter) + decoration).strip()
            signature = " ".join(filter(None, (label, name)))
            if mask_defaults and _field(parameter, "default_value"):
                signature += " = …"
            result.append(signature)
        elif parameter.type in {"variadic_parameter_declaration", "variadic_parameter"}:
            result.append(_syntax(parameter) if mask_defaults else "…")
    if node and any(child.type == "..." for child in node.children):
        result.append("..." if mask_defaults else "…")
    return ", ".join(result)


def _cpp_template(node: Node) -> str:
    params = _field(node, "parameters")
    constraint = next((c for c in node.named_children if c.type == "requires_clause"), None)
    return " ".join(filter(None, ("template" + _syntax(params), _syntax(constraint))))


def _cpp_method_clause(node: Node, declaration: Node) -> str:
    for child in node.named_children:
        if child.type in {"delete_method_clause", "default_method_clause"}:
            return "= delete" if child.type == "delete_method_clause" else "= default"
    following = False
    for child in node.children:
        if child == declaration:
            following = True
        elif following:
            if child.type in {",", ";"}:
                break
            # This is the language's pure-virtual marker, not an initializer value.
            if child.type == "number_literal" and _text(child) == "0":
                return "= 0"
    return ""


def _cpp_initialized(node: Node, declaration: Node) -> bool:
    if _field(declaration, "value"):
        return True
    # One declaration may contain initialized and uninitialized fields together.
    following = False
    for child in node.children:
        if child == declaration:
            following = True
        elif following:
            if child.type in {",", ";"}:
                break
            if child.type in {"=", "initializer_list"}:
                return True
    return False


def _cpp_canonical(node, declaration, function, name, decoration, return_decoration, kind):
    declared_type = " ".join(
        filter(
            None,
            (
                " ".join(
                    _text(c)
                    for c in node.named_children
                    if c.type == "type_qualifier" and _text(c) in {"const", "volatile"}
                ),
                _type(_field(node, "type")),
            ),
        )
    )
    qualifiers = _cpp_qualifiers(node, function)
    field_declarator = (
        _field(declaration, "declarator") if declaration.type == "init_declarator" else declaration
    )
    identifier = field_declarator
    while child := _declarator_child(identifier):
        identifier = child
    field_decoration = _syntax(field_declarator, {identifier.id: ""})
    if function:
        params = _cpp_parameters(_field(function, "parameters"), mask_defaults=True)
        if kind == "field":
            signature = f"{name} : {declared_type} {field_decoration}".rstrip()
        else:
            # Preserve pointer cv/ref structure outside the function declarator.
            return_decoration = (
                ""
                if declaration.type == "operator_cast"
                else _syntax(declaration, {function.id: ""})
            )
            return_type = declared_type + return_decoration
            trailing = next(
                (c for c in function.named_children if c.type == "trailing_return_type"),
                None,
            )
            if trailing:
                return_type = " ".join(_type(c) for c in trailing.named_children)
            signature = f"{name}({params})" + (f" : {return_type}" if return_type else "")
            suffix = [
                ("noexcept(…)" if c.named_children else "noexcept")
                if c.type == "noexcept"
                else _syntax(c)
                for c in function.named_children
                if c.type
                in {
                    "type_qualifier",
                    "virtual_specifier",
                    "ref_qualifier",
                    "noexcept",
                    "requires_clause",
                    "attribute_declaration",
                }
            ]
            if suffix:
                signature += " " + " ".join(suffix)
            clause = _cpp_method_clause(node, declaration)
            if clause:
                signature += " " + clause
    else:
        signature = f"{name} : {declared_type}{field_decoration}".rstrip(" :")
        bitfield = next((c for c in node.named_children if c.type == "bitfield_clause"), None)
        if bitfield:
            signature += " : …"
    if kind == "field" and _cpp_initialized(node, declaration):
        signature += " = …"
    prefix = [
        q
        for q in qualifiers
        if q
        in {
            "static",
            "virtual",
            "inline",
            "explicit",
            "constexpr",
            "consteval",
            "constinit",
            "mutable",
            "thread_local",
            "friend",
        }
    ]
    explicit = next(
        (c for c in node.named_children if c.type == "explicit_function_specifier"), None
    )
    if explicit:
        prefix = [_syntax(explicit) if q == "explicit" else q for q in prefix]
    prefix.extend(_syntax(c) for c in node.named_children if c.type == "attribute_declaration")
    return " ".join([*prefix, signature]), qualifiers


def _cpp_member(node: Node, visibility: str, declarations: list, line_offset=0) -> list[str]:
    if node.type == "template_declaration":
        start = len(declarations)
        rows = [
            m
            for c in node.named_children
            if c.type != "template_parameter_list"
            for m in _cpp_member(c, visibility, declarations, line_offset)
        ]
        for declaration in declarations[start:]:
            declaration["text"] = _cpp_template(node) + " " + declaration["text"]
            declaration.update(_lines(node, line_offset))
        return rows
    if node.type == "friend_declaration":
        start = len(declarations)
        rows = [
            m
            for c in node.named_children
            for m in _cpp_member(c, visibility, declarations, line_offset)
        ]
        for declaration in declarations[start:]:
            declaration["text"] = "friend " + declaration["text"]
            declaration["qualifiers"] = _unique(["friend", *declaration["qualifiers"]])
            declaration.update(_lines(node, line_offset))
        return rows
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
        kind = "field"
        if function:
            params = _cpp_parameters(_field(function, "parameters"))
            inner = _field(function, "declarator")
            if inner and inner.type == "parenthesized_declarator":
                # A function pointer field is a field, not a method.
                signature = f"{name} : {declared_type} ({decoration})({params})"
            else:
                kind = "method"
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
        canonical, qualifiers = _cpp_canonical(
            node,
            declaration,
            function,
            name,
            decoration,
            return_decoration,
            kind,
        )
        declarations.append(
            _declaration(
                node,
                name,
                kind,
                canonical,
                visibility,
                qualifiers,
                line_offset,
            )
        )
    return result


def _cpp_class(node: Node, scope: tuple[str, ...], line_offset: int) -> dict | None:
    name, body = _field(node, "name"), _field(node, "body")
    if not name or not body:
        return None
    if _syntax(name) != _text(name):
        # Masking a specialized identity could merge distinct declarations (e.g.
        # Store<1> and Store<2>). Fail this bounded selection instead of leaking
        # the value or pretending those declarations have the same name.
        raise ValueError("含字面量或表达式的 C++ 类模板特化名称暂不展示；请改选类型参数声明")
    kind = "struct" if node.type == "struct_specifier" else "class"
    visibility = "public" if kind == "struct" else "private"
    members, declarations = [], []
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
            members.extend(_cpp_member(child, visibility, declarations, line_offset))
    bases = []
    for clause in node.named_children:
        if clause.type == "base_class_clause":
            bases.extend(
                {"name": _type(c), "kind": "extends", **_lines(c, line_offset)}
                for c in clause.named_children
                if c.type != "access_specifier" and c.type != "attribute_declaration"
            )
    declaration = _syntax(node, {body.id: ""})
    parent = node.parent
    while parent and parent.type == "template_declaration":
        declaration = _cpp_template(parent) + " " + declaration
        parent = parent.parent
    return {
        "name": "::".join((*scope, _text(name))),
        "declaration": declaration,
        "kind": kind,
        **_lines(node, line_offset),
        "language": "cpp",
        "members": members,
        "member_declarations": declarations,
        "bases": [base["name"] for base in bases],
        "base_declarations": bases,
    }


def _parse_cpp(text: str) -> dict:
    root = Parser(_CPP_LANGUAGE).parse(text.encode("utf-8")).root_node
    if root.has_error:
        raise ValueError(
            "C/C++ 声明不完整或宏语法暂不支持；请改选普通声明文件或查看明确标记的局部设计图"
        )
    classes, imports, import_declarations = [], [], []
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
                target = _text(path)[1:-1]
                imports.append(target)
                import_declarations.append({"name": target, **_lines(node)})
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
    return {
        "classes": classes,
        "imports": _unique(imports),
        "import_declarations": import_declarations,
        "limitations": _unique(limitations),
    }


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


def _ts_parameter(
    node: Node,
    *,
    mask_defaults: bool = False,
    include_modifiers: bool = False,
) -> str:
    if node.type not in {"required_parameter", "optional_parameter"}:
        return _ts_name(node)
    name = (_syntax if mask_defaults else _ts_name)(_field(node, "pattern"))
    if node.type == "optional_parameter":
        name += "?"
    annotation = _type(_field(node, "type"))
    signature = name + (f" : {annotation}" if annotation else "")
    if mask_defaults and _field(node, "value"):
        signature += " = …"
    if include_modifiers:
        prefix = [
            _text(c) for c in node.children if c.type in {"accessibility_modifier", "readonly"}
        ]
        signature = " ".join([*prefix, signature])
    return signature


def _ts_qualifiers(node: Node) -> list[str]:
    recognized = {"static", "readonly", "abstract", "async", "override", "declare", "get", "set"}
    return _unique(c.type for c in node.children if c.type in recognized)


def _ts_members(body: Node | None, line_offset: int = 0) -> tuple[list[str], list[dict]]:
    members, declarations = [], []

    def add(member, name, kind, signature, canonical=None):
        marker = _ts_visibility(member)
        qualifiers = _ts_qualifiers(member)
        canonical = canonical if canonical is not None else signature
        if kind == "field" and _field(member, "value"):
            canonical += " = …"
        canonical = " ".join([*(q for q in qualifiers if q not in {"get", "set"}), canonical])
        members.append(f"{marker} {signature}")
        declarations.append(
            _declaration(
                member,
                name,
                kind,
                canonical,
                {"+": "public", "#": "protected", "-": "private"}[marker],
                qualifiers,
                line_offset,
            )
        )

    for member in _children(body):
        name = _ts_name(_field(member, "name"))
        annotation = _type(_field(member, "type"))
        if member.type in {"public_field_definition", "property_signature"}:
            optional = "?" if any(c.type == "?" for c in member.children) else ""
            definite = "!" if any(c.type == "!" for c in member.children) else ""
            add(
                member,
                name,
                "field",
                f"{name}{optional}" + (f" : {annotation}" if annotation else ""),
                f"{name}{optional}{definite}" + (f" : {annotation}" if annotation else ""),
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
            label = accessor + " " + name if accessor else name
            returns = _type(_field(member, "return_type")) or annotation
            canonical_params = ", ".join(
                _ts_parameter(c, mask_defaults=True, include_modifiers=True)
                for c in _children(parameters)
                if c.type != "comment"
            )
            canonical_label = label
            if any(c.type == "*" for c in member.children):
                canonical_label = "*" + canonical_label
            if any(c.type == "?" for c in member.children):
                canonical_label += "?"
            canonical_label += _syntax(_field(member, "type_parameters"))
            add(
                member,
                name,
                "method",
                f"{label}({params})" + (f" : {returns}" if returns else ""),
                f"{canonical_label}({canonical_params})" + (f" : {returns}" if returns else ""),
            )
            if name == "constructor":
                # Parameter properties really declare fields, but their initializer values do not.
                for parameter in _children(parameters):
                    if any(
                        c.type in {"accessibility_modifier", "readonly"} for c in parameter.children
                    ):
                        add(
                            parameter,
                            _ts_name(_field(parameter, "pattern")),
                            "field",
                            _ts_parameter(parameter),
                        )
        elif member.type == "index_signature":
            index_type = _type(_field(member, "index_type"))
            add(
                member,
                f"[{name}]",
                "field",
                f"[{name} : {index_type}]" + (f" : {annotation}" if annotation else ""),
            )
    return members, declarations


def _ts_bases(node: Node, line_offset: int = 0) -> list[dict]:
    def lexical_name(value):
        return (
            value is not None
            and value.type
            in {
                "identifier",
                "type_identifier",
                "property_identifier",
                "member_expression",
                "nested_identifier",
            }
            and all(lexical_name(child) for child in value.named_children)
        )

    bases = []
    for child in node.named_children:
        if child.type == "extends_type_clause":
            bases.extend(
                {"name": _type(c), "kind": "extends", **_lines(c, line_offset)}
                for c in child.named_children
            )
        elif child.type == "class_heritage":
            for clause in child.named_children:
                if clause.type == "extends_clause":
                    value = _field(clause, "value")
                    # Do not render a mixin call and its arbitrary arguments as a base type.
                    if lexical_name(value):
                        bases.append(
                            {
                                "name": _text(value) + _syntax(_field(clause, "type_arguments")),
                                "kind": "extends",
                                **_lines(clause, line_offset),
                            }
                        )
                elif clause.type == "implements_clause":
                    bases.extend(
                        {"name": _type(c), "kind": "implements", **_lines(c, line_offset)}
                        for c in clause.named_children
                    )
    return bases


def _parse_ts(
    text: str,
    *,
    tsx: bool = False,
    line_offset: int = 0,
    language: str = "typescript",
) -> dict:
    root = Parser(_TSX_LANGUAGE if tsx else _TS_LANGUAGE).parse(text.encode("utf-8")).root_node
    if root.has_error:
        raise ValueError("TS/JS 声明无法可靠解析；请修复语法或缩小选择范围")
    classes, imports, import_declarations = [], [], []
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
                target = _text(source)[1:-1]
                imports.append(target)
                import_declarations.append({"name": target, **_lines(node, line_offset)})
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
                members, declarations = _ts_members(_field(node, "body"), line_offset)
                bases = _ts_bases(node, line_offset)
                classes.append(
                    {
                        "name": ".".join((*scope, _text(name))),
                        "declaration": _syntax(node, {_field(node, "body").id: ""}),
                        "kind": "interface" if node.type == "interface_declaration" else "class",
                        **_lines(node, line_offset),
                        "language": language,
                        "members": members,
                        "member_declarations": declarations,
                        "bases": [base["name"] for base in bases],
                        "base_declarations": bases,
                    }
                )
        elif node.type in containers:
            pending.extend((c, scope) for c in reversed(node.named_children))
    return {
        "classes": classes,
        "imports": _unique(imports),
        "import_declarations": import_declarations,
        "limitations": [_TS_LIMIT],
    }


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
        "import_declarations": [],
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
            result["import_declarations"].append(
                {
                    "name": attrs["src"],
                    "line": offset + 1,
                    "end_line": offset + 1,
                }
            )
            result["limitations"].append("Vue script src 仅作为边界依赖，未读取外部脚本。")
            continue
        parsed = _parse_ts(
            script,
            tsx=language in {"tsx", "jsx"},
            line_offset=offset,
            language="typescript" if language in {"ts", "typescript", "tsx"} else "javascript",
        )
        result["classes"].extend(parsed["classes"])
        result["imports"].extend(parsed["imports"])
        result["import_declarations"].extend(parsed["import_declarations"])
    result["imports"] = _unique(result["imports"])
    result["limitations"] = _unique(result["limitations"])
    return result


def parse_classes(path: str, text: str) -> dict:
    """Extract named declarations from text; path is a language hint, never opened.

    The result contains classes and typed member/base/import declarations with
    1-based source ranges, legacy display rows, and explicit language limitations. Invalid syntax is rejected
    rather than presenting a recovered parse as verified class coverage.
    """
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in CPP_SUFFIXES:
        return _parse_cpp(text)
    if suffix in TS_SUFFIXES:
        return _parse_ts(
            text,
            tsx=suffix in {".tsx", ".jsx"},
            language="javascript" if suffix in {".js", ".jsx", ".mjs", ".cjs"} else "typescript",
        )
    if suffix == ".vue":
        return _parse_vue(text)
    raise ValueError(f"暂不支持此类提取语言：{suffix or '(无后缀)'}")
