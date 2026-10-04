"""A terminal candidate stays immutable across independent app connections."""

from copy import deepcopy

import pytest
from evograph.domain.models import PendingQuestion, Project, now, uid
from evograph.domain.plan_contracts import candidate_hash, review_subjects
from evograph.infrastructure.database import ConflictError, Database
from evograph.infrastructure.plan_candidates import CandidateStore


def ready_candidate(store, before):
    staged = before.model_copy(deep=True)
    staged.target_draft = "Candidate change"
    return store.save({
        "id": uid(), "project": staged.model_dump(), "created_at": now(),
        "base_revision": before.revision, "status": "ready",
        "metrics": {"tokens": 2, "elapsed_seconds": 0.1},
        "report": {"findings": [], "semantic": {
            "candidate_hash": candidate_hash(staged), "summary": "Fixture review",
            "checks": [{"subject": subject, "verdict": "supported",
                        "reason": "Fixture preserves the requested plan",
                        "counterexample": "No counterexample in fixture"}
                       for subject in review_subjects(staged)],
        }},
    })


def storage_snapshot(db):
    with db.connect() as connection:
        return {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in ("projects", "project_planning_extensions", "plan_candidates", "events")
        }


@pytest.mark.parametrize("terminal", ["discarded", "applied_noop", "applied_commit"])
@pytest.mark.parametrize("operation", ["save", "pause", "consume", "noop", "pending_noop", "commit"])
def test_late_writes_cannot_replace_terminal_candidate(tmp_path, terminal, operation):
    db = Database(tmp_path / "test.sqlite")
    before = db.create(Project(name="Terminal storage", unified_planning=True))
    in_flight = CandidateStore(db)
    other_window = CandidateStore(Database(db.path))
    record = ready_candidate(in_flight, before)
    if terminal == "discarded":
        other_window.discard(before.id, record["id"])
    elif terminal == "applied_noop":
        # A completed explanation does not advance canonical revision: the
        # candidate's durable status must protect it, independently of that CAS.
        other_window.noop(record, before, "finished", pending=False)
    else:
        other_window.commit(record, before, "finished")
    snapshot = storage_snapshot(db)
    stale = deepcopy(record)
    with pytest.raises(ConflictError, match="最终状态"):
        if operation == "save":
            in_flight.save(stale)
        elif operation in {"pause", "consume"}:
            question = PendingQuestion(prompt="Too late", category="decision")
            in_flight.pause_question(stale, question if operation == "pause" else None)
        elif operation in {"noop", "pending_noop"}:
            in_flight.noop(stale, before, "late", pending=operation == "pending_noop")
        else:
            in_flight.commit(stale, before, "late")
    assert storage_snapshot(db) == snapshot
    assert stale == record
    if terminal == "discarded":
        assert in_flight.latest(before.id) is None
        # Refusing the checkpoint must also prevent the formerly ready snapshot
        # from subsequently publishing the discarded change.
        with pytest.raises(ConflictError):
            in_flight.commit(record, before, "after-rejected-checkpoint")
        assert db.get(before.id) == before


@pytest.mark.parametrize("operation", ["pause", "noop"])
def test_missing_candidate_cannot_write_question_or_turn_receipt(tmp_path, operation):
    db = Database(tmp_path / "test.sqlite")
    before = db.create(Project(name="Missing candidate", unified_planning=True))
    store = CandidateStore(db)
    record = ready_candidate(store, before)
    record["id"] = "not-saved"
    snapshot = storage_snapshot(db)
    with pytest.raises(ValueError, match="候选规划不存在"):
        if operation == "pause":
            store.pause_question(record, PendingQuestion(prompt="Orphan", category="decision"))
        else:
            store.noop(record, before, "orphan", pending=False)
    assert storage_snapshot(db) == snapshot


def test_new_candidate_can_follow_terminal_history(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    before = db.create(Project(name="Next candidate", unified_planning=True))
    store = CandidateStore(db)
    old = ready_candidate(store, before)
    store.discard(before.id, old["id"])
    terminal = store.get(old["id"])
    fresh = ready_candidate(store, before)
    question = PendingQuestion(prompt="Choose a direction", category="decision")
    fresh["project"]["question"] = question.model_dump()
    fresh = store.save(fresh)
    paused = store.pause_question(fresh, question)
    assert db.get(before.id).question == question
    resumed = store.pause_question(paused, None, status="generating")
    assert db.get(before.id).question is None
    assert resumed["base_revision"] == db.get(before.id).revision
    assert store.get(old["id"]) == terminal
    assert store.latest(before.id)["id"] == fresh["id"]
