"""Saved receipt identities only; no provider or live project data is used."""

import json

import pytest
from conftest import proposal
from evograph.application.turn_summary import build_turn_summary, read_turn_summary
from evograph.domain.models import BehaviorRevision, Milestone, Project, TargetVersion
from test_turn_summary import outcome, run


def project(scope="target"):
    behavior = BehaviorRevision(
        id="B1",
        behavior_key="parse",
        version=1,
        statement="**All** links\n<script>x</script>",
        acceptance_scope=scope,
        owner="M1",
    )
    return Project(
        id="P",
        name="History",
        created_at="2026-10-01T00:00:00Z",
        behaviors=[behavior],
        milestones=[
            Milestone(id="M1", title="Library", intent="Direct use", behavior_revision_ids=["B1"])
        ],
        targets=[
            TargetVersion(
                number=3,
                statement="Check links",
                required_behavior_ids=["B1"] if scope == "target" else [],
            )
        ],
    )


def revise(before, *, scope=None, owner=None, statement=None, finalize=True):
    after = before.model_copy(deep=True)
    original = after.behaviors[0]
    new = original.model_copy(
        update={
            "id": "B2",
            "version": 2,
            "supersedes": "B1",
            "statement": original.statement if statement is None else statement,
            "acceptance_scope": scope or original.acceptance_scope,
            "owner": owner or original.owner,
        }
    )
    after.behaviors.append(new)
    after.milestones[0].behavior_revision_ids = []
    if new.owner != after.milestones[0].id:
        after.milestones.append(Milestone(id=new.owner, title="New owner", intent="Own it"))
    after.milestone(new.owner).behavior_revision_ids = [new.id]
    if finalize:
        after.targets.append(
            TargetVersion(
                number=4,
                statement="Check links",
                required_behavior_ids=["B2"] if new.acceptance_scope == "target" else [],
            )
        )
    return after


def details(before, after, status="completed"):
    return build_turn_summary(before, after, "turn", status)["contract_details"]


@pytest.mark.parametrize(
    "initial,final",
    [
        ("target", "milestone"),
        ("milestone", "target"),
        ("milestone", "milestone"),
        ("target", "target"),
    ],
)
def test_active_ids_and_target_membership_are_independent(initial, final):
    before = project(initial)
    after = revise(before, scope=final, statement="Only simple links")
    receipt = details(before, after)
    assert receipt["project_id"] == "P"
    assert receipt["project_created_at"] == before.created_at
    assert (receipt["before_target_version"], receipt["after_target_version"]) == (3, 4)
    (item,) = receipt["behaviors"]
    assert item["before"] == {
        "active_id": "B1",
        "required_id": "B1" if initial == "target" else None,
    }
    assert item["after"] == {"active_id": "B2", "required_id": "B2" if final == "target" else None}
    assert item["fields"] == ["statement"] + (["acceptance_scope"] if initial != final else [])
    assert not item["restored"]
    # Only identities and changed fields are duplicated, never whole states or text.
    assert "Only simple links" not in json.dumps(receipt)
    assert "<script>" not in json.dumps(receipt)


def test_owner_transfer_keeps_exact_both_revision_ids():
    before = project()
    (item,) = details(before, revise(before, owner="M2"))["behaviors"]
    assert item["fields"] == ["owner"]
    assert item["before"]["active_id"] == "B1"
    assert item["after"]["active_id"] == "B2"


def test_add_remove_restore_and_net_noop_use_active_membership_not_history():
    before = project("milestone")
    removed = before.model_copy(deep=True)
    removed.milestones = []
    (removal,) = details(before, removed)["behaviors"]
    assert removal["before"]["active_id"] == "B1"
    assert removal["after"] == {"active_id": None, "required_id": None}
    (restored,) = details(removed, before)["behaviors"]
    assert restored["restored"]
    assert restored["after"]["active_id"] == "B1"  # Genuine same-ID restoration.
    blank = removed.model_copy(deep=True)
    blank.behaviors = []
    (added,) = details(blank, before)["behaviors"]
    assert not added["restored"]
    assert details(before, before)["behaviors"] == []
    history_only = before.model_copy(deep=True)
    history_only.behaviors.append(
        before.behaviors[0].model_copy(update={"id": "UNUSED", "version": 99})
    )
    assert not build_turn_summary(before, history_only, "turn", "completed")["changed"]


def test_revision_only_change_does_not_claim_text_scope_or_owner_changes():
    before = project()
    (item,) = details(before, revise(before))["behaviors"]
    assert item["fields"] == []
    assert item["before"]["active_id"] != item["after"]["active_id"]


def test_missing_historical_records_keep_exact_orphan_ids():
    before = project()
    before.behaviors = []
    after = before.model_copy(deep=True)
    after.milestones = []
    after.targets.append(TargetVersion(number=4, statement="Check links", required_behavior_ids=[]))
    (item,) = details(before, after)["behaviors"]
    assert item["behavior_key"] is None
    assert item["before"] == {"active_id": "B1", "required_id": "B1"}
    assert item["after"] == {"active_id": None, "required_id": None}
    assert item["fields"] == []


@pytest.mark.parametrize("status", ["failed", "stopped", "waiting"])
def test_partial_saved_active_revision_does_not_imply_target_finalization(status):
    before = project()
    after = revise(before, statement="Saved but target not finalized", finalize=False)
    summary = build_turn_summary(before, after, "turn", status)
    assert summary["status"] == status and summary["changed"]
    assert summary["changes"]["target"] is None
    receipt = summary["contract_details"]
    assert receipt["before_target_version"] == receipt["after_target_version"] == 3
    (item,) = receipt["behaviors"]
    assert item["after"] == {"active_id": "B2", "required_id": "B1"}
    assert item["fields"] == ["statement"]


def test_failed_finalize_persists_actual_active_and_committed_target_ids(app, planned, monkeypatch):
    def fail(_):
        raise ValueError("Finalization failed")

    monkeypatch.setattr(app.graph, "finalize", fail)
    node = proposal().milestones[0].model_dump()
    node["behaviors"][0]["statement"] = "Already saved acceptance"
    summary = outcome(app, planned.id, run(app, planned.id, [("update_milestone", node)]))
    saved = app.db.get(planned.id)
    assert summary["status"] == "failed"
    (item,) = summary["contract_details"]["behaviors"]
    assert item["after"]["active_id"] == saved.milestones[0].behavior_revision_ids[0]
    assert item["after"]["required_id"] == planned.targets[-1].required_behavior_ids[0]
    assert item["after"]["active_id"] != item["after"]["required_id"]
    assert read_turn_summary(json.dumps(summary), summary["turn_id"]) == summary


def test_additive_receipt_keeps_legacy_reader_and_exact_unchanged_target_reference():
    before = project("milestone")
    after = revise(before, statement="Changed local acceptance", finalize=False)
    summary = build_turn_summary(before, after, "turn", "completed")
    assert summary["changes"]["target"] is None
    assert summary["contract_details"]["after_target_version"] == 3
    legacy = {key: value for key, value in summary.items() if key != "contract_details"}
    assert read_turn_summary(json.dumps(legacy), "turn") == legacy
