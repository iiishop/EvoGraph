"""Direct transaction routes preserve the same immutable admission/audit seal."""
from copy import deepcopy

import pytest
from evograph.application.plan_ir import PlanDelta, submit_plan_delta
from evograph.domain.models import PendingQuestion
from evograph.domain.plan_contracts import candidate_hash
from evograph.infrastructure.database import ConflictError
from test_retained_acceptance import admitted, change


@pytest.mark.parametrize("route", ["question", "noop"])
@pytest.mark.parametrize("mutation", ["drop_entry", "change_binding", "delete_audit"])
def test_direct_question_and_noop_routes_reject_seal_mutation_atomically(app, route, mutation):
    ctx, db, canonical = admitted(app)
    submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "check", "mechanism": "Different helper",
                                                "acceptance_changes": [change(db)]}]))
    old = deepcopy(db.record)
    bad = deepcopy(old)
    if mutation == "drop_entry":
        bad.pop("retained_acceptance")
    elif mutation == "change_binding":
        bad["retained_acceptance"]["claims"][0]["binding"]["mechanism"] = "FORGED"
    else:
        bad["compilations"] = []
    with pytest.raises((ValueError, ConflictError)):
        if route == "question":
            db.store.pause_question(bad, PendingQuestion(prompt="Keep?", category="decision", options=["Keep", "Drop"]))
        else:
            db.store.noop(bad, canonical, "offline-noop", pending=True)
    assert db.store.get(old["id"]) == old
    assert app.db.get(canonical.id) == canonical


def test_legitimate_question_answer_and_cancel_keep_entry_and_audits(app):
    ctx, db, canonical = admitted(app)
    submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "check", "mechanism": "Different helper",
                                                "acceptance_changes": [change(db)]}]))
    old = deepcopy(db.record)
    question = PendingQuestion(prompt="Keep?", category="decision", options=["Keep", "Drop"])
    asked = deepcopy(old)
    asked["project"]["question"] = question.model_dump()
    asked = db.store.pause_question(asked, question)
    assert asked["retained_acceptance"] == old["retained_acceptance"]
    assert asked["compilations"] == old["compilations"]
    assert app.db.get(canonical.id).question.id == question.id
    answer = deepcopy(asked)
    answer["project"]["question"] = None
    answer = db.store.pause_question(answer, None, status="generating")
    assert app.db.get(canonical.id).question is None
    assert candidate_hash(app.db.get(canonical.id)) == candidate_hash(canonical)
    cancelled = db.store.save({**answer, "status": "stopped"})
    assert cancelled["retained_acceptance"] == old["retained_acceptance"]
    assert cancelled["compilations"] == old["compilations"]
