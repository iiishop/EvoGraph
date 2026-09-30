"""Reference candidates used by the Agent composer @-mention picker."""

from ..infrastructure.repository import files, readable, root_path


class ReferenceService:
    """Expose project assets and safe repository paths without reading file contents."""

    def __init__(self, db):
        self.db = db

    def list(self, project_id: str):
        project = self.db.get(project_id)
        items = [
            {
                "id": asset.id,
                "kind": "attachment",
                "name": asset.name,
                "label": asset.name,
                "detail": "项目资料",
                "path": "",
            }
            for asset in project.attachments
        ]
        truncated = False
        if project.repository:
            root = root_path(project.repository)
            for path in files(root):
                if not readable(path):
                    continue
                relative = path.relative_to(root).as_posix()
                items.append(
                    {
                        "id": f"repo:{relative}",
                        "kind": "repository",
                        "name": relative,
                        "label": relative,
                        "detail": "仓库文件",
                        "path": relative,
                    }
                )
        return {"items": items, "truncated": truncated}
