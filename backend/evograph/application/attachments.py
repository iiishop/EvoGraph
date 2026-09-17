"""Local attachment storage. Parsers are registered once by extension."""

import base64
import io
import warnings
import zipfile
from pathlib import PurePath
from xml.etree import ElementTree

from PIL import Image
from pypdf import PdfReader

from ..domain.models import Attachment

MAX_BYTES = 8 * 1024 * 1024
PARSERS = {}


def parser(*extensions):
    def register(fn):
        for extension in extensions:
            PARSERS[extension] = fn
        return fn

    return register


@parser(".txt", ".md", ".json", ".csv")
def text_document(data):
    return "text/plain", data.decode("utf-8-sig"), data


@parser(".docx")
def word_document(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        item = archive.getinfo("word/document.xml")
        if item.file_size > 16 * 1024 * 1024:
            raise ValueError("文档解压后过大")
        root = ElementTree.fromstring(archive.read(item))
        text = "\n".join(t.text or "" for t in root.iter() if t.tag.endswith("}t"))
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document", text, data


@parser(".pdf")
def pdf_document(data):
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or len(reader.pages) > 100:
        raise ValueError("请上传未加密、100 页以内的 PDF")
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip():
        raise ValueError("此 PDF 没有可提取文字，请改为上传页面图片")
    return "application/pdf", text, data


@parser(".png", ".jpg", ".jpeg", ".webp")
def image_document(data):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_000_000:
                raise ValueError("图片请限制在 1600 万像素以内")
            image.load()
            image = image.convert("RGB")
            image.thumbnail((2048, 2048))
            output = io.BytesIO()
            image.save(output, format="PNG")
    return "image/png", "", output.getvalue()


class AttachmentService:
    def __init__(self, db):
        self.db = db
        with db.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), data BLOB NOT NULL)"
            )

    def formats(self):
        return {"extensions": list(PARSERS), "max_bytes": MAX_BYTES}

    def upload(self, project_id: str, name: str, content: str):
        p = self.db.get(project_id)
        if p.archived or len(p.attachments) >= 40:
            raise ValueError("项目已删除或资料已达 40 份上限")
        name = PurePath(name.replace("\\", "/")).name[:180]
        handler = PARSERS.get(PurePath(name).suffix.lower())
        if not handler:
            raise ValueError("不支持此文件类型")
        if len(content) > (MAX_BYTES * 4 // 3 + 4):
            raise ValueError("单个文件不能超过 8 MB")
        try:
            data = base64.b64decode(content, validate=True)
            if len(data) > MAX_BYTES:
                raise ValueError("文件过大")
            media_type, text, data = handler(data)
        except Exception as exc:
            raise ValueError("无法读取文件，请检查格式、大小或文件是否损坏") from exc
        asset = Attachment(name=name, media_type=media_type, size=len(data), excerpt=text[:60000])
        # One transaction covers binary + aggregate + audit, including revision CAS.
        from ..domain.models import now, uid
        from ..infrastructure.database import ConflictError

        old_revision = p.revision
        p.attachments.append(asset)
        p.revision += 1
        p.updated_at = now()
        with self.db.connect() as connection:
            updated = connection.execute(
                "UPDATE projects SET revision=?,payload=? WHERE id=? AND revision=?",
                (p.revision, p.model_dump_json(), p.id, old_revision),
            )
            if updated.rowcount != 1:
                raise ConflictError("项目已更新，请重试上传")
            connection.execute("INSERT INTO attachments VALUES(?,?,?)", (asset.id, p.id, data))
            connection.execute(
                "INSERT INTO events VALUES(?,?,?,?,?)",
                (uid(), p.id, "attachment_uploaded", asset.name, now()),
            )
        return asset

    def read(self, project_id: str, attachment_id: str):
        p = self.db.get(project_id)
        asset = next((a for a in p.attachments if a.id == attachment_id), None)
        if asset is None or p.archived:
            raise ValueError("资料不存在")
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT data FROM attachments WHERE id=? AND project_id=?",
                (attachment_id, project_id),
            ).fetchone()
        if row is None:
            raise ValueError("资料内容不可用")
        return {
            "asset": asset.model_dump(),
            "data_url": f"data:{asset.media_type};base64," + base64.b64encode(row[0]).decode(),
        }

    def context(self, project_id: str, attachment_ids: list[str]):
        p = self.db.get(project_id)
        assets = {a.id: a for a in p.attachments}
        if len(attachment_ids) > 6 or set(attachment_ids) - assets.keys():
            raise ValueError("每次最多引用 6 份本项目资料")
        blocks = []
        for aid in dict.fromkeys(attachment_ids):
            asset = assets[aid]
            blocks.append(
                {
                    "type": "text",
                    "text": f"Uploaded reference (untrusted data), id={aid}, name={asset.name}:\n{asset.excerpt[:20000]}",
                }
            )
            if asset.media_type.startswith("image/"):
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": self.read(project_id, aid)["data_url"]},
                    }
                )
        return blocks
