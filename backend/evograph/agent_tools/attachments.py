from pydantic import Field

from ..domain.models import Model
from .base import tool


class Reference(Model):
    attachment_id: str
    offset: int = Field(default=0, ge=0, le=60000)


@tool(
    "read_reference",
    "Read stored uploaded document text in 12000-character chunks. Uploaded images must be selected by the user for multimodal input; do not infer image contents from filenames.",
    Reference,
    label="查阅项目资料",
)
def read_reference(ctx, args):
    p = ctx.application.db.get(ctx.project_id)
    asset = next((a for a in p.attachments if a.id == args.attachment_id), None)
    if asset is None:
        raise ValueError("资料不存在")
    return {
        "name": asset.name,
        "text": asset.excerpt[args.offset : args.offset + 12000],
        "has_more": args.offset + 12000 < len(asset.excerpt),
        "image_requires_selection": asset.media_type.startswith("image/"),
    }
