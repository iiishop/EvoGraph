"""Experimental contracts live beside, never inside, the strict legacy aggregate."""

import json
import sqlite3
from copy import deepcopy

import pytest
from evograph.domain.models import Model, PendingQuestion, Project, now, uid
from evograph.domain.plan_contracts import IntentSource, candidate_hash, review_subjects
from evograph.infrastructure.database import ConflictError, Database
from evograph.infrastructure.plan_candidates import CandidateStore
from pydantic import create_model

EXTENSIONS = {"unified_planning", "plan_contract"}
# The historical aggregate forbids unrecognized top-level fields. Its nested
# target, milestone, behavior and architecture contracts remain the same.
LegacyProject = create_model(
    "LegacyProject",
    __base__=Model,
    **{
        name: (field.annotation, deepcopy(field))
        for name, field in Project.model_fields.items()
        if name not in EXTENSIONS
    },
)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.sqlite")


def rich_project(**overrides):
    return Project.model_validate({
        "name": "兼容规划",
        "unified_planning": True,
        "targets": [{"number": 1, "statement": "Check local links", "required_behavior_ids": ["b1"]}],
        "behaviors": [{"id": "b1", "behavior_key": "links", "version": 1,
                       "statement": "Reports local broken links", "owner": "CHECK"}],
        "milestones": [{"id": "CHECK", "title": "Checker", "intent": "Check local links",
                        "scope": ["checker.py"], "resources": ["checker"],
                        "change_types": ["general"], "behavior_revision_ids": ["b1"]}],
        "architectures": [{"number": 1, "summary": "Local checker",
                           "technologies": [{"area": "runtime", "choice": "Python",
                                             "rationale": "Run locally"}],
                           "diagram": {"id": "architecture", "title": "Checker",
                                       "nodes": [{"id": "checker", "label": "Checker"}]}}],
        "plan_contract": {
            "sources": [{"id": "intent", "text": "Check local links"}],
            "requirements": [{"id": "r1", "source_id": "intent", "quote": "Check local links"}],
            "bindings": [{"behavior_key": "links", "behavior_revision_id": "b1",
                          "requirement_ids": ["r1"], "mechanism": "Resolve local destinations"}],
        },
        **overrides,
    })


def raw_rows(db):
    with db.connect() as connection:
        return [tuple(row) for row in connection.execute("SELECT * FROM projects ORDER BY rowid")]


def assert_legacy_payload(db, project):
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM projects WHERE id=?", (project.id,)).fetchone()
        extension = connection.execute(
            "SELECT payload FROM project_planning_extensions WHERE project_id=?", (project.id,)
        ).fetchone()
    raw = json.loads(row["payload"])
    assert not EXTENSIONS & raw.keys()
    assert LegacyProject.model_validate_json(row["payload"]).model_dump() == project.model_dump(
        exclude=EXTENSIONS
    )
    assert row["revision"] == raw["revision"] == project.revision
    assert json.loads(extension[0]) == project.model_dump(mode="json", include=EXTENSIONS)


def ready_candidate(store, before):
    staged = before.model_copy(deep=True)
    staged.target_draft = "Updated candidate target"
    # Candidate opt-in flags are not authoritative and must not disable the
    # actual canonical flag when commit hydrates the current project.
    staged.unified_planning = False
    staged.plan_contract.sources.append(IntentSource(id="followup", text="Updated goal"))
    fingerprint = candidate_hash(staged)
    return store.save({
        "id": uid(), "project": staged.model_dump(), "created_at": now(),
        "base_revision": before.revision, "status": "ready", "metrics": {
            "tokens": 7, "elapsed_seconds": 0.5,
        },
        "report": {"findings": [], "semantic": {
            "candidate_hash": fingerprint, "summary": "Reviewed exact candidate",
            "checks": [{"subject": subject, "verdict": "supported",
                        "reason": "Preserves existing requirement",
                        "counterexample": "No additional failure found"}
                       for subject in review_subjects(staged)],
        }},
    })


def test_create_save_and_identity_retries_keep_legacy_shape_and_hydrate_sidecar(db):
    project = db.create(rich_project(creation_key="request"))
    archived = db.create(rich_project(name="Archived", archived=True))
    plain = db.create(Project(name="Legacy-compatible default"))
    for item in (project, archived, plain):
        assert_legacy_payload(db, item)
        assert db.get(item.id) == item
    assert db.list_projects() == [project, plain]
    assert db.list_projects(include_archived=True) == [project, archived, plain]
    assert db.create_with_outcome(Project(name="Retry", creation_key="request")).project == project
    project.description = "Core update"
    project.unified_planning = False
    project.plan_contract.sources.append(IntentSource(id="extra", text="Retained while disabled"))
    db.save(project, "test")
    assert_legacy_payload(db, project)
    assert db.get(project.id) == project
    project.unified_planning = True
    db.save(project, "reenabled")
    assert db.get(project.id).unified_planning
    assert_legacy_payload(db, project)


def test_startup_only_migrates_inline_fields_without_normalizing_other_data(tmp_path):
    path = tmp_path / "pre-sidecar.sqlite"
    legacy = [json.dumps({"id": f"old-{index}", "name": "旧项目", "revision": index},
                         ensure_ascii=False, indent=2) for index in range(9)]
    experimental = rich_project(revision=31).model_dump(mode="json")
    # Both of these would change under ordinary Project validation/serialization.
    experimental["question"] = {"id": "q", "prompt": "Question without category", "options": []}
    experimental["source_milestones"] = [{"id": "SRC_old", "title": "Old directory"}]
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, revision INTEGER, payload TEXT)")
        connection.executemany("INSERT INTO projects VALUES(?,?,?)", [
            (f"old-{index}", index, payload) for index, payload in enumerate(legacy)
        ])
        connection.execute("INSERT INTO projects VALUES(?,?,?)", (
            experimental["id"], 31, json.dumps(experimental, ensure_ascii=False),
        ))
        connection.execute("CREATE TABLE source_history(id TEXT PRIMARY KEY, payload TEXT)")
        connection.execute("INSERT INTO source_history VALUES('untouched','exact source evidence')")
    database = Database(path)
    rows = raw_rows(database)
    assert [row[2] for row in rows[:9]] == legacy
    assert rows[-1][1] == 31
    assert json.loads(rows[-1][2]) == {k: v for k, v in experimental.items() if k not in EXTENSIONS}
    with database.connect() as connection:
        extensions = connection.execute("SELECT * FROM project_planning_extensions").fetchall()
        assert len(extensions) == 1
        assert json.loads(extensions[0]["payload"]) == {
            k: v for k, v in experimental.items() if k in EXTENSIONS
        }
        assert tuple(connection.execute("SELECT * FROM source_history").fetchone()) == (
            "untouched", "exact source evidence",
        )
    assert not database.get("old-0").unified_planning
    assert database.get(experimental["id"]).unified_planning
    assert raw_rows(Database(path)) == rows  # Reopening is byte-preserving and idempotent.


def test_partial_inline_migration_preserves_other_existing_extension(db):
    project = db.create(rich_project())
    with db.connect() as connection:
        payload = json.loads(db.storage_payload(project))
        payload["unified_planning"] = False
        connection.execute("UPDATE projects SET payload=? WHERE id=?", (json.dumps(payload), project.id))
    reopened = Database(db.path)
    migrated = reopened.get(project.id)
    assert not migrated.unified_planning
    assert migrated.plan_contract == project.plan_contract
    assert migrated.revision == project.revision
    assert_legacy_payload(reopened, migrated)


def test_migration_failure_rolls_back_all_inline_rows_and_extensions(tmp_path):
    path = tmp_path / "migration-failure.sqlite"
    projects = [rich_project(name=f"Experimental {index}") for index in range(2)]
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, revision INTEGER, payload TEXT)")
        connection.executemany("INSERT INTO projects VALUES(?,?,?)", [
            (p.id, p.revision, p.model_dump_json()) for p in projects
        ])

    class FailingMigration(Database):
        writes = 0

        def _write_planning_extensions(self, connection, project_id, extensions):
            super()._write_planning_extensions(connection, project_id, extensions)
            self.writes += 1
            if self.writes == 2:
                raise OSError("Synthetic sidecar failure")

    with pytest.raises(OSError, match="sidecar"):
        FailingMigration(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM projects ORDER BY rowid").fetchall() == [
            (p.id, p.revision, p.model_dump_json()) for p in projects
        ]
        assert connection.execute("SELECT * FROM project_planning_extensions").fetchall() == []
    assert Database(path).list_projects() == projects


def test_candidate_commit_hydrates_enabled_flag_and_commits_contract_and_core(db):
    before = db.create(rich_project())
    store = CandidateStore(db)
    record = ready_candidate(store, before)
    result, applied, summary = store.commit(record, before, "turn")
    assert result.unified_planning
    assert result.plan_contract.sources[-1].id == "followup"
    assert result.target_draft == "Updated candidate target"
    assert result.revision == before.revision + 1
    assert result.metrics["model_tokens"] == 7
    assert db.get(before.id) == result
    assert applied["status"] == store.get(record["id"])["status"] == "applied"
    assert json.loads(db.turn_result_detail(before.id, "turn")) == summary
    assert_legacy_payload(db, result)
    # Rich candidate snapshots remain intentionally isolated from old clients.
    assert store.get(record["id"])["project"]["plan_contract"]["sources"][-1]["id"] == "followup"


@pytest.mark.parametrize("table,action", [("plan_candidates", "UPDATE"), ("events", "INSERT")])
def test_later_candidate_or_receipt_failure_also_rolls_back_core_and_sidecar(db, table, action):
    before = db.create(rich_project())
    store = CandidateStore(db)
    record = ready_candidate(store, before)
    rows = raw_rows(db)
    with db.connect() as connection:
        connection.execute(
            f"CREATE TRIGGER fail_receipt BEFORE {action} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'Synthetic receipt failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="receipt failure"):
        store.commit(record, before, "failed-receipt")
    assert raw_rows(db) == rows
    assert db.get(before.id) == before
    assert store.get(record["id"]) == record
    assert db.events(before.id) == []


def test_legacy_client_can_read_and_save_core_without_losing_extensions(db):
    before = db.create(rich_project())
    store = CandidateStore(db)
    record = ready_candidate(store, before)
    with db.connect() as connection:
        row = connection.execute("SELECT payload FROM projects WHERE id=?", (before.id,)).fetchone()
        legacy = LegacyProject.model_validate_json(row[0])
        legacy.description = "Edited by published client"
        legacy.revision += 1
        connection.execute(
            "UPDATE projects SET revision=?,payload=? WHERE id=? AND revision=?",
            (legacy.revision, legacy.model_dump_json(), legacy.id, before.revision),
        )
    current = db.get(before.id)
    assert current.description == legacy.description
    assert current.revision == legacy.revision
    assert current.unified_planning and current.plan_contract == before.plan_contract
    assert_legacy_payload(db, current)
    with pytest.raises(ConflictError):
        store.commit(record, before, "pre-legacy-write")
    assert db.get(before.id) == current


def test_question_pause_noop_and_discard_preserve_canonical_extensions(db):
    before = db.create(rich_project())
    store = CandidateStore(db)
    record = ready_candidate(store, before)
    question = PendingQuestion(prompt="Which path?", category="decision", options=["A", "B"])
    record["project"]["question"] = question.model_dump()
    record = store.save(record)
    paused = store.pause_question(record, question)
    current = db.get(before.id)
    assert current.question == question and current.unified_planning
    assert current.plan_contract == before.plan_contract
    assert paused["base_revision"] == current.revision
    assert_legacy_payload(db, current)
    rows = raw_rows(db)
    store.noop(paused, current, "pending-turn", pending=True)
    assert raw_rows(db) == rows
    assert db.get(before.id) == current
    store.discard(before.id, record["id"])
    current = db.get(before.id)
    assert current.question is None and current.unified_planning
    assert current.plan_contract == before.plan_contract
    assert store.get(record["id"])["status"] == "discarded"
    assert_legacy_payload(db, current)


@pytest.mark.parametrize("operation", ["create", "save", "commit", "pause", "discard"])
def test_sidecar_failure_rolls_back_core_candidate_and_event_writes(db, monkeypatch, operation):
    before = db.create(rich_project())
    store = CandidateStore(db)
    record = ready_candidate(store, before)
    question = PendingQuestion(prompt="Need input", category="missing_design_input")
    if operation == "discard":
        record["project"]["question"] = question.model_dump()
        record = store.save(record)
        record = store.pause_question(record, question)
        before = db.get(before.id)
    rows = raw_rows(db)
    events = db.events(before.id)
    original_record = store.get(record["id"])
    write_extensions = db._write_planning_extensions

    def fail_after_sidecar_write(*args):
        write_extensions(*args)
        raise OSError("Synthetic sidecar failure")

    monkeypatch.setattr(db, "_write_planning_extensions", fail_after_sidecar_write)
    with pytest.raises(OSError, match="sidecar"):
        if operation == "create":
            db.create(rich_project(name="Cannot commit"))
        elif operation == "save":
            changed = before.model_copy(deep=True)
            changed.description = "Cannot commit"
            changed.unified_planning = False
            db.save(changed, "cannot_commit")
        elif operation == "commit":
            store.commit(record, before, "cannot_commit")
        elif operation == "pause":
            store.pause_question(record, question)
        else:
            store.discard(before.id, record["id"])
    assert raw_rows(db) == rows
    assert db.get(before.id) == before
    assert db.events(before.id) == events
    assert store.get(record["id"]) == original_record


@pytest.mark.parametrize("operation", ["save", "commit", "pause", "noop"])
def test_stale_revision_cannot_replace_core_or_extensions(db, operation):
    before = db.create(rich_project())
    store = CandidateStore(db)
    record = ready_candidate(store, before)
    current = db.get(before.id)
    current.description = "Concurrent winner"
    current.plan_contract.sources.append(IntentSource(id="winner", text="Concurrent source"))
    db.save(current, "winner")
    rows, events = raw_rows(db), db.events(before.id)
    with pytest.raises(ConflictError):
        if operation == "save":
            db.save(before, "stale")
        elif operation == "commit":
            store.commit(record, before, "stale")
        elif operation == "pause":
            store.pause_question(record, PendingQuestion(prompt="Stale", category="decision"))
        else:
            store.noop(record, before, "stale", pending=False)
    assert raw_rows(db) == rows
    assert db.get(current.id) == current
    assert db.events(current.id) == events
    assert store.get(record["id"])["status"] == "ready"


@pytest.mark.parametrize("method", ["get", "list_projects"])
def test_reads_hydrate_core_and_sidecar_from_one_snapshot(db, monkeypatch, method):
    before = db.create(rich_project())
    updated = before.model_copy(deep=True)
    updated.description = "Concurrent core"
    updated.unified_planning = False
    updated.plan_contract.sources.append(IntentSource(id="concurrent", text="Concurrent sidecar"))
    decode = db.decode_project
    called = False

    def change_between_core_and_sidecar(connection, payload):
        nonlocal called
        if not called:
            called = True
            db.save(updated, "concurrent")
        return decode(connection, payload)

    with monkeypatch.context() as patch:
        patch.setattr(db, "decode_project", change_between_core_and_sidecar)
        result = db.get(before.id) if method == "get" else db.list_projects()[0]
    assert result == before
    assert db.get(before.id) == updated


def test_source_analysis_question_lookup_is_exact_and_legacy_json_safe(db):
    project = db.create(Project(name="Source questions"))
    other = db.create(Project(name="Other project"))
    db.save(project, "source_analysis_question", "pre-JSON legacy detail")
    db.save(project, "source_analysis_question", json.dumps({"question_id": "q1"}))
    db.save(project, "agent_question", json.dumps({"question_id": "q2"}))
    db.save(other, "source_analysis_question", json.dumps({"question_id": "q3"}))
    assert db.is_source_analysis_question(project.id, "q1")
    assert not db.is_source_analysis_question(project.id, "q")
    assert not db.is_source_analysis_question(project.id, "q2")
    assert not db.is_source_analysis_question(project.id, "q3")
    assert not db.is_source_analysis_question(other.id, "q1")
