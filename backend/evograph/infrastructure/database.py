import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from ..domain.models import Model, Project, now, uid


class ConflictError(ValueError):
    pass


class ProjectCreationResult(Model):
    outcome: Literal["created", "reused_request", "existing_repository"]
    project: Project


class Database:
    """One transaction per aggregate mutation, with an optimistic revision guard."""

    PLANNING_EXTENSION_FIELDS = frozenset({"unified_planning", "plan_contract"})

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_version VALUES(1);
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS project_planning_extensions(project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS message_documents(message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_project ON messages(project_id, created_at);
                CREATE INDEX IF NOT EXISTS events_project ON events(project_id, created_at);
                CREATE INDEX IF NOT EXISTS events_turn_result ON events(
                    project_id,
                    json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END, '$.turn_id')
                ) WHERE kind='agent_turn_finished';
            """)
            self._migrate_planning_extensions(db)

    def _migrate_planning_extensions(self, db: sqlite3.Connection) -> None:
        """Remove only experimental top-level fields, without normalizing legacy data."""
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT id,payload FROM projects").fetchall():
            payload = json.loads(row["payload"])
            extensions = {
                field: payload.pop(field)
                for field in self.PLANNING_EXTENSION_FIELDS
                if field in payload
            }
            if not extensions:
                # Do not reserialize old rows: validators/defaults can transform
                # historical data, and older clients must retain their exact bytes.
                continue
            previous = db.execute(
                "SELECT payload FROM project_planning_extensions WHERE project_id=?",
                (row["id"],),
            ).fetchone()
            if previous:
                extensions = {**json.loads(previous[0]), **extensions}
            self._write_planning_extensions(db, row["id"], extensions)
            db.execute(
                "UPDATE projects SET payload=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), row["id"]),
            )

    def decode_project(self, db: sqlite3.Connection, payload: str) -> Project:
        """Hydrate the aggregate using the caller's existing read/write transaction."""
        data = json.loads(payload)
        row = db.execute(
            "SELECT payload FROM project_planning_extensions WHERE project_id=?", (data["id"],)
        ).fetchone()
        if row:
            extensions = json.loads(row[0])
            data.update({
                field: extensions[field]
                for field in self.PLANNING_EXTENSION_FIELDS
                if field in extensions
            })
        return Project.model_validate(data)

    def read_project(self, db: sqlite3.Connection, project_id: str) -> Project:
        row = db.execute("SELECT payload FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ValueError("项目不存在")
        return self.decode_project(db, row[0])

    def storage_payload(self, project: Project) -> str:
        """Keep projects.payload readable by strict pre-unified-planning clients."""
        return project.model_dump_json(exclude=self.PLANNING_EXTENSION_FIELDS)

    def _write_planning_extensions(
        self, db: sqlite3.Connection, project_id: str, extensions: dict
    ) -> None:
        db.execute(
            "INSERT INTO project_planning_extensions VALUES(?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET payload=excluded.payload",
            (project_id, json.dumps(extensions, ensure_ascii=False)),
        )

    def write_project(
        self, db: sqlite3.Connection, project: Project, *, expected_revision: int | None = None
    ) -> None:
        """Write both representations; the caller owns their single commit boundary."""
        if expected_revision is None:
            db.execute(
                "INSERT INTO projects VALUES(?,?,?)",
                (project.id, project.revision, self.storage_payload(project)),
            )
        else:
            updated = db.execute(
                "UPDATE projects SET revision=?,payload=? WHERE id=? AND revision=?",
                (project.revision, self.storage_payload(project), project.id, expected_revision),
            )
            if updated.rowcount != 1:
                raise ConflictError("项目已被其他操作更新，请刷新后重试")
        self._write_planning_extensions(
            db, project.id, project.model_dump(mode="json", include=self.PLANNING_EXTENSION_FIELDS)
        )

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
            db.execute("BEGIN")
            projects = [
                self.decode_project(db, r[0])
                for r in db.execute("SELECT payload FROM projects ORDER BY rowid")
            ]
            return [p for p in projects if include_archived or not p.archived]

    def get(self, project_id: str) -> Project:
        with self.connect() as db:
            db.execute("BEGIN")
            return self.read_project(db, project_id)

    def create(self, project: Project) -> Project:
        return self.create_with_outcome(project).project

    def create_with_outcome(
        self, project: Project, *, prepare: Callable[[Project], None] | None = None
    ) -> ProjectCreationResult:
        """Resolve identity and repository ownership in the committing transaction."""
        with self.connect() as db:
            # Serialize the uniqueness check across windows/processes, not just UI clicks.
            db.execute("BEGIN IMMEDIATE")
            existing = [
                self.decode_project(db, row[0])
                for row in db.execute("SELECT payload FROM projects ORDER BY rowid")
            ]
            # A retry belongs to its original request, even when its draft now
            # names another occupied repository. Archived identities stay
            # archived: recovery must neither resurrect nor duplicate them.
            for other in existing:
                if project.creation_key and project.creation_key == other.creation_key:
                    return ProjectCreationResult(outcome="reused_request", project=other)
            for other in existing:
                if project.id == other.id or (
                    project.is_demo and other.is_demo and not other.archived
                ):
                    return ProjectCreationResult(outcome="reused_request", project=other)
            # Service validation may depend on a directory that disappeared
            # after the original commit. Only a new identity needs preparing.
            if prepare:
                prepare(project)
            for other in existing:
                same_repository = (
                    project.repository
                    and other.repository
                    and os.path.normcase(os.path.realpath(project.repository))
                    == os.path.normcase(os.path.realpath(other.repository))
                )
                if not other.archived and same_repository:
                    return ProjectCreationResult(outcome="existing_repository", project=other)
            self.write_project(db, project)
        # The context manager has committed before a caller can observe this
        # result. No post-commit read is required to confirm the saved record.
        return ProjectCreationResult(outcome="created", project=project)

    def save(self, project: Project, kind: str, detail: str = "") -> Project:
        old_revision = project.revision
        project.revision += 1
        project.updated_at = now()
        with self.connect() as db:
            # Rebinding and restoring must preserve the same repository invariant
            # as creation, including concurrent changes in other windows.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM projects WHERE id=?", (project.id,)
            ).fetchone()
            previous = self.decode_project(db, row[0]) if row else None
            if (
                previous
                and not project.archived
                and project.repository
                and (previous.archived or previous.repository != project.repository)
            ):
                repository = os.path.normcase(os.path.realpath(project.repository))
                for candidate in db.execute("SELECT payload FROM projects WHERE id!=?", (project.id,)):
                    other = self.decode_project(db, candidate[0])
                    if (
                        not other.archived
                        and other.repository
                        and os.path.normcase(os.path.realpath(other.repository)) == repository
                    ):
                        raise ValueError("此仓库已有活跃项目，请打开现有项目或选择其他仓库")
            self.write_project(db, project, expected_revision=old_revision)
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?)", (uid(), project.id, kind, detail, now())
            )
        return project

    def message(
        self, project_id: str, role: str, content: str, composer_document: dict | None = None
    ):
        message_id = uid()
        created_at = now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?)",
                (message_id, project_id, role, content, created_at),
            )
            if composer_document:
                # A separate table keeps old clients' five-column inserts valid.
                # Content and its document commit together, never partially.
                db.execute(
                    "INSERT INTO message_documents VALUES(?,?)",
                    (message_id, json.dumps(composer_document, ensure_ascii=False)),
                )

        return {
            "id": message_id,
            "project_id": project_id,
            "role": role,
            "content": content,
            "created_at": created_at,
            "composer_document": composer_document or None,
        }

    def turn_result_detail(self, project_id: str, turn_id: str) -> str | None:
        # Exact indexed lookup includes old receipts outside the activity window.
        # The CASE also keeps pre-JSON legacy events safe during index migration.
        with self.connect() as db:
            row = db.execute(
                "SELECT detail FROM events WHERE project_id=? AND kind='agent_turn_finished' "
                "AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END, "
                "'$.turn_id')=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (project_id, turn_id),
            ).fetchone()
        return row[0] if row else None

    def is_source_analysis_question(self, project_id: str, question_id: str) -> bool:
        with self.connect() as db:
            return db.execute(
                "SELECT 1 FROM events WHERE project_id=? AND kind='source_analysis_question' "
                "AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END, "
                "'$.question_id')=? LIMIT 1",
                (project_id, question_id),
            ).fetchone() is not None

    def messages(self, project_id: str):
        with self.connect() as db:
            return [
                {
                    **dict(r),
                    "composer_document": json.loads(r["composer_document"])
                    if r["composer_document"]
                    else None,
                }
                for r in db.execute(
                    "SELECT m.*, d.document AS composer_document FROM messages m "
                    "LEFT JOIN message_documents d ON d.message_id=m.id "
                    "WHERE m.project_id=? ORDER BY m.created_at, m.rowid",
                    (project_id,),
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
