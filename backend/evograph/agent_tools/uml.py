import hashlib
from importlib.resources import files

from ..application.class_detail import MAX_FILE_BYTES, selection, validate_class_source
from ..application.uml import STYLE
from ..domain.models import UmlDiagram
from ..infrastructure.repository import root_path
from .base import tool


@tool(
    "save_uml",
    STYLE
    + "\nFull user drawing specification:\n"
    + files("evograph").joinpath("resources/uml-style.md").read_text(encoding="utf-8"),
    UmlDiagram,
    label="维护 UML 设计图",
    effect="updated",
)
def save_uml(ctx, args):
    project = ctx.application.db.get(ctx.project_id)
    if args.kind == "class":
        selection(project, args.component_ids, args.architecture_revision, args.source_refs or None)
        validate_class_source(args.source)
    if args.origin in {"source", "mixed"}:
        missing = set(args.source_refs) - set(ctx.receipts)
        if missing:
            raise ValueError("先读取类图引用的源码：" + ", ".join(sorted(missing)))
        root = root_path(project.repository)
        for name in args.source_refs:
            path = (root / name).resolve()
            if args.kind == "class" and path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("源码范围过大，请缩小范围：" + name)
            if (
                not path.is_relative_to(root)
                or ctx.receipts.get(name) != hashlib.sha256(path.read_bytes()).hexdigest()
            ):
                raise ValueError("源码已经变化，请重新读取：" + name)
    return ctx.application.uml.save(ctx.project_id, args)
