import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..domain.models import Project, now, uid


class ConflictError(ValueError):
    pass


class Database:
    """One transaction per aggregate mutation, with an optimistic revision guard."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_version VALUES(1);
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_project ON messages(project_id, created_at);
                CREATE INDEX IF NOT EXISTS events_project ON events(project_id, created_at);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_projects(self, include_archived: bool = False) -> list[Project]:
        with self.connect() as db:
            projects = [
                Project.model_validate_json(r[0])
                for r in db.execute("SELECT payload FROM projects ORDER BY rowid")
            ]
            return [p for p in projects if include_archived or not p.archived]

    def get(self, project_id: str) -> Project:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ValueError("项目不存在")
        return Project.model_validate_json(row[0])

    def create(self, project: Project) -> Project:
        with self.connect() as db:
            # Serialize the uniqueness check across windows/processes, not just UI clicks.
            db.execute("BEGIN IMMEDIATE")
            existing = [
                Project.model_validate_json(row[0])
                for row in db.execute("SELECT payload FROM projects")
            ]
            import os

            for other in existing:
                same_repository = (
                    project.repository
                    and other.repository
                    and os.path.normcase(os.path.realpath(project.repository))
                    == os.path.normcase(os.path.realpath(other.repository))
                )
                same_request = project.creation_key and project.creation_key == other.creation_key
                if not other.archived and (
                    same_repository
                    or same_request
                    or project.id == other.id
                    or (project.is_demo and other.is_demo)
                ):
                    return other
            db.execute(
                "INSERT INTO projects VALUES(?,?,?)",
                (project.id, project.revision, project.model_dump_json()),
            )
        return project

    def save(self, project: Project, kind: str, detail: str = "") -> Project:
        old_revision = project.revision
        project.revision += 1
        project.updated_at = now()
        with self.connect() as db:
            updated = db.execute(
                "UPDATE projects SET revision=?,payload=? WHERE id=? AND revision=?",
                (project.revision, project.model_dump_json(), project.id, old_revision),
            )
            if updated.rowcount != 1:
                raise ConflictError("项目已被其他操作更新，请刷新后重试")
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?)", (uid(), project.id, kind, detail, now())
            )
        return project

    def message(self, project_id: str, role: str, content: str):
        with self.connect() as db:
            db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?)", (uid(), project_id, role, content, now())
            )

    def messages(self, project_id: str):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM messages WHERE project_id=? ORDER BY created_at", (project_id,)
                )
            ]

    def events(self, project_id: str):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM events WHERE project_id=? ORDER BY created_at DESC LIMIT 100",
                    (project_id,),
                )
            ]

    def setting(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        with self.connect() as db:
            db.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                (key, json.dumps(value)),
            )
