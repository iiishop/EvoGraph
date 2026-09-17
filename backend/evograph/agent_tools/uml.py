import hashlib
from importlib.resources import files

from ..application.uml import STYLE
from ..domain.models import UmlDiagram
from ..infrastructure.repository import root_path
from .base import tool


@tool("save_uml", STYLE + "\nFull user drawing specification:\n" + files("evograph").joinpath("resources/uml-style.md").read_text(encoding="utf-8"), UmlDiagram, label="维护 UML 设计图", effect="updated")
def save_uml(ctx, args):
    if args.origin == "source":
        missing = set(args.source_refs) - set(ctx.inspected)
        if missing:
            raise ValueError("先读取类图引用的源码：" + ", ".join(sorted(missing)))
        root = root_path(ctx.application.db.get(ctx.project_id).repository)
        for name in args.source_refs:
            path = (root / name).resolve()
            if not path.is_relative_to(root) or ctx.receipts.get(name) != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError("源码已经变化，请重新读取：" + name)
    return ctx.application.uml.save(ctx.project_id, args)
