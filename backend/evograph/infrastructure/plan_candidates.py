"""Durable, isolated planning candidates and one CAS publication transaction."""
import json
from copy import deepcopy

from ..domain.models import Project, now, uid
from ..domain.plan_contracts import (
    PLANNING_FIELDS,
    candidate_hash,
)
from .database import ConflictError


class CandidateStore:
    def __init__(self, db):
        self.db = db
        with db.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS plan_candidates(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS candidates_project ON plan_candidates(project_id, created_at);
            """)

    def _writable_candidate(self, connection, candidate_id, *, required=True):
        """Called under the writer lock so terminal records cannot be resurrected."""
        row = connection.execute(
            "SELECT payload FROM plan_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        saved = json.loads(row[0]) if row else None
        if saved and saved["status"] in {"applied", "discarded"}:
            raise ConflictError("候选已应用或放弃，不能覆盖其最终状态")
        if required and saved is None:
            raise ValueError("候选规划不存在")
        return saved

    def save(self, record):
        data = deepcopy(record)
        data["candidate_hash"] = candidate_hash(Project.model_validate(data["project"]))
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._writable_candidate(connection, data["id"], required=False)
            connection.execute("INSERT INTO plan_candidates VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (
                    data["id"], data["project"]["id"], json.dumps(data, ensure_ascii=False), data["created_at"],
                ))
        return data

    def get(self, candidate_id):
        with self.db.connect() as connection:
            row = connection.execute("SELECT payload FROM plan_candidates WHERE id=?", (candidate_id,)).fetchone()
        if not row:
            raise ValueError("候选规划不存在")
        return json.loads(row[0])

    def latest(self, project_id):
        with self.db.connect() as connection:
            row = connection.execute("SELECT payload FROM plan_candidates WHERE project_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (project_id,)).fetchone()
        if not row:
            return None
        record = json.loads(row[0])
        return None if record["status"] == "discarded" else record

    def event(self, project_id, kind, detail):
        with self.db.connect() as connection:
            connection.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                uid(), project_id, kind, json.dumps(detail, ensure_ascii=False), now(),
            ))

    def pause_question(self, record, question, *, status="needs_resolution"):
        """Question identity and resumed base advance together, never rebase a changed plan."""
        data = deepcopy(record)
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._writable_candidate(connection, data["id"])
            current = self.db.read_project(connection, record["project"]["id"])
            if current.revision != record["base_revision"]:
                raise ConflictError("正式规划已改变，候选问题未覆盖当前状态")
            current.question = question
            current.revision += 1
            current.updated_at = now()
            data.update(base_revision=current.revision, status=status)
            self.db.write_project(connection, current, expected_revision=record["base_revision"])
            connection.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (
                json.dumps(data, ensure_ascii=False), data["id"],
            ))
            connection.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                uid(), current.id, "agent_question" if question else "agent_answer", question.prompt if question else "已回答候选问题", now(),
            ))
        return data

    def discard(self, project_id, candidate_id):
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM plan_candidates WHERE project_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (project_id,)).fetchone()
            record = json.loads(row[0]) if row else None
            if not record or record["id"] != candidate_id:
                raise ConflictError("此候选已不是当前版本，请刷新后操作")
            if record["status"] == "applied":
                raise ValueError("已应用的方案不能通过放弃候选撤销")
            current = self.db.read_project(connection, project_id)
            question = record["project"].get("question")
            if current.question and question and question["id"] == current.question.id:
                old_revision = current.revision
                current.question = None
                current.revision += 1
                current.updated_at = now()
                self.db.write_project(connection, current, expected_revision=old_revision)
            record["status"] = "discarded"
            connection.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (
                json.dumps(record, ensure_ascii=False), candidate_id,
            ))
        return record

    def noop(self, record, before, turn_id, pending):
        from ..application.plan_validation import build_validation_receipt
        from ..application.turn_summary import build_turn_summary
        data = deepcopy(record)
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._writable_candidate(connection, data["id"])
            current = self.db.read_project(connection, before.id)
            if current.revision != data["base_revision"]:
                raise ConflictError("正式规划已改变，候选保留")
            summary = build_turn_summary(before, current, turn_id, "completed")
            data.update(status="needs_resolution" if pending else "applied", turn_summary=summary)
            data["candidate_hash"] = candidate_hash(Project.model_validate(data["project"]))
            data["validation_receipt"] = build_validation_receipt(Project.model_validate(data["project"]))
            if not pending:
                data["applied_revision"] = current.revision
            connection.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (
                json.dumps(data, ensure_ascii=False), data["id"],
            ))
            connection.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                uid(), current.id, "agent_turn_finished", json.dumps(summary, ensure_ascii=False), now(),
            ))
        return data, summary

    def commit(self, record, before, turn_id):
        from ..application.plan_harness import replay_harness, seal_snapshot
        from ..application.plan_review import validate_batch_certificate
        from ..application.plan_validation import build_validation_receipt
        from ..application.turn_summary import build_turn_summary

        # Re-read the durable exact candidate inside the transaction. There is no
        # check-then-save window between validation receipt and candidate identity.
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved = self._writable_candidate(connection, record["id"])
            if not saved or saved["status"] != "ready" or saved["candidate_hash"] != record["candidate_hash"]:
                raise ValueError("候选已变化，需要重新校验")
            if saved.get("work_units") is not None:
                from ..application.plan_units import all_units_complete
                if not all_units_complete(saved):
                    raise ValueError("调度单元尚未全部完成，不能提交部分候选")
            staged = Project.model_validate(saved["project"])
            if candidate_hash(staged) != saved["candidate_hash"]:
                raise ValueError("候选内容指纹不一致")
            row = connection.execute("SELECT payload FROM projects WHERE id=?", (before.id,)).fetchone()
            current = self.db.decode_project(connection, row[0]) if row else None
            if not current or current.revision != saved["base_revision"]:
                raise ConflictError("正式规划已改变；候选保留，但不能覆盖较新的状态")
            if current.archived or not current.unified_planning:
                raise ConflictError("项目状态已改变，候选未应用")
            if candidate_hash(current) != candidate_hash(before):
                raise ConflictError("评审前快照与当前正式规划不一致，候选需要重新校验")
            snapshot = seal_snapshot(before, staged, saved)

            def replay_model(sealed, certificate):
                # This synchronous callback only replays the exact durable model
                # certificate. No provider or application handles enter plugins.
                if not saved.get("batch_reviews") or certificate != saved["batch_reviews"][-1]:
                    raise ValueError("插件评审证书与保存的原始记录不一致")
                return validate_batch_certificate(
                    sealed.before_project(), sealed.candidate_project(), saved)

            run = replay_harness(snapshot, saved.get("harness_run"), replay_model)
            if run.decision != "apply":
                raise ValueError("当前检查策略仍有未解决项，候选未应用")
            saved["harness_commit_replay"] = {
                "kind": "synchronous_certificate_replay", "run": run.model_dump(mode="json")}

            saved["validation_receipt"] = build_validation_receipt(staged, harness_run=run)
            result = current.model_copy(deep=True)
            for field in PLANNING_FIELDS:
                setattr(result, field, deepcopy(getattr(staged, field)))
            result.question = None
            result.metrics["model_tokens"] += saved["metrics"]["tokens"]
            result.metrics["planning_seconds"] += saved["metrics"]["elapsed_seconds"]
            result.revision += 1
            result.updated_at = now()
            outcome = build_turn_summary(before, result, turn_id, "completed")
            saved.update(status="applied", applied_revision=result.revision, turn_summary=outcome)
            self.db.write_project(connection, result, expected_revision=current.revision)
            connection.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (
                json.dumps(saved, ensure_ascii=False), saved["id"],
            ))
            connection.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                uid(), result.id, "agent_turn_finished", json.dumps(outcome, ensure_ascii=False), now(),
            ))
        return result, saved, outcome
