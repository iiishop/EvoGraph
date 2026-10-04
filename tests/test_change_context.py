"""Saved change facts are bounded, source-safe, and independent of semantic judgement."""

import json
from copy import deepcopy
from time import perf_counter

import pytest
from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.domain.change_context import _before_values, change_context
from evograph.domain.models import BehaviorRevision, Milestone, Project, ProposedMilestone


def milestone(mid, prerequisites=(), **changes):
    return Milestone(
        id=mid,
        title=f"Deliver {mid}",
        intent=f"Provide the saved {mid} contract",
        scope=[f"{mid}.py"],
        dependencies=list(prerequisites),
        dependency_reasons={dep: f"{mid} consumes {dep}" for dep in prerequisites},
        dependency_types={dep: "implementation" for dep in prerequisites},
        **changes,
    )


def project(*nodes, source_nodes=()):
    behaviors = [
        BehaviorRevision(
            id=f"{node.id}-v1", behavior_key=f"{node.id}.contract", version=1,
            statement=f"{node.id} meets its saved contract", owner=node.id,
        )
        for node in nodes
    ]
    for node, behavior in zip(nodes, behaviors, strict=True):
        node.behavior_revision_ids = [behavior.id]
    return Project(
        id="change-context-fixture", name="Saved change facts", revision=10,
        milestones=list(nodes), behaviors=behaviors, source_milestones=list(source_nodes),
    )


def source(mid, prerequisites=()):
    return milestone(
        mid, prerequisites, origin="source", status="IMPLEMENTED",
        source_baseline_id="observed-baseline", source_refs=[f"src/{mid}.py:1-12"],
        source_behaviors=[{
            "key": f"{mid}.observed", "statement": f"{mid} appears to provide an existing API",
            "source_refs": [f"src/{mid}.py:3-8"],
        }],
    )


def node_map(context):
    return {node["id"]: node for node in context["nodes"]}


def before_summary(node):
    if "before" in node:
        return deepcopy(node["before"])
    summary = deepcopy(node["current"])
    summary.update(node.get("before_overrides", {}))
    for field in node.get("before_absent_fields", []):
        summary.pop(field)
    return summary


def contracts_for(context, mid):
    return [row for row in context["contracts"] if row["node_id"] == mid]


def contract_values(context, mid, state="current"):
    return [
        row.get("before", row["current"]) if state == "before" else row["current"]
        for row in contracts_for(context, mid)
        if (row.get("before", row["current"]) if state == "before" else row["current"])
        is not None
    ]


def field_row(context, mid, field):
    rows = [
        row for row in context["node_fields"]
        if row["node_id"] == mid and row["field"] == field
    ]
    assert len(rows) == 1
    return rows[0]


def field_value(context, mid, field, state="current"):
    row = field_row(context, mid, field)
    assert row[f"{state}_present"]
    return row.get("before", row.get("current")) if state == "before" else row["current"]


def serialized(context):
    return json.dumps(context, ensure_ascii=False).encode("utf-8")


def test_removed_edge_keeps_old_metadata_and_detached_ancestor_contracts():
    before = project(
        milestone("foundation"), milestone("bridge", ["foundation"]),
        milestone("delivery", ["bridge"]), milestone("consumer", ["delivery"]),
    )
    before.milestone("delivery").dependency_types["bridge"] = "migration"
    before.milestone("delivery").dependency_reasons["bridge"] = "Requires the old migration API"
    current = before.model_copy(deep=True)
    current.revision += 1
    delivery = current.milestone("delivery")
    delivery.dependencies, delivery.dependency_reasons, delivery.dependency_types = [], {}, {}

    result = change_context(before, current)

    assert result["status"] == "changed"
    assert result["before_revision"] == 10
    assert result["project_revision"] == 11
    old_edge = {
        "prerequisite_id": "bridge", "dependent_id": "delivery",
        "reason": "Requires the old migration API", "kind": "migration",
    }
    assert result["dependency_changes"] == [{
        "prerequisite_id": "bridge", "dependent_id": "delivery",
        "before": old_edge, "current": None,
    }]
    nodes = node_map(result)
    assert set(nodes) == {"foundation", "bridge", "delivery", "consumer"}
    assert before_summary(nodes["delivery"])["prerequisite_ids"] == ["bridge"]
    assert nodes["delivery"]["current"]["prerequisite_ids"] == []
    assert field_value(result, "delivery", "prerequisites", "before") == [old_edge]
    assert field_value(result, "delivery", "prerequisites") == []
    assert contract_values(result, "foundation")[0]["owner"] == "foundation"
    assert nodes["consumer"]["current"]["prerequisite_ids"] == ["delivery"]
    assert field_value(result, "consumer", "prerequisites")[0]["prerequisite_id"] == "delivery"
    assert all("before" not in nodes[mid] for mid in ("foundation", "bridge", "consumer"))
    assert all("before_overrides" not in nodes[mid] for mid in ("foundation", "bridge", "consumer"))
    assert result["milestone_changes"] == [{
        "id": "delivery", "change": "updated", "fields": ["prerequisites"],
    }]
    assert result["coverage"]["eligible_node_count"] == 4
    assert result["coverage"]["omitted_node_count"] == 0
    assert result["truncated"] is False


def test_behavior_change_includes_transitive_foundations_and_downstream_consumers():
    before = project(
        milestone("root"), milestone("upstream", ["root"]),
        milestone("changed", ["upstream"]), milestone("consumer", ["changed"]),
        milestone("final", ["consumer"]), milestone("unrelated"),
    )
    current = before.model_copy(deep=True)
    active = next(behavior for behavior in current.behaviors if behavior.owner == "changed")
    active.statement = "The revised interface includes a compatibility token"

    result = change_context(before, current)

    assert set(node_map(result)) == {"root", "upstream", "changed", "consumer", "final"}
    assert result["dependency_changes"] == []
    assert result["milestone_changes"] == [{
        "id": "changed", "change": "updated", "fields": ["active_behaviors"],
    }]
    assert node_map(result)["changed"]["current"]["active_behavior_revision_ids"] == [active.id]
    assert contract_values(result, "changed") == [active.model_dump()]
    assert contract_values(result, "changed", "before") == [before.behaviors[2].model_dump()]
    assert result["coverage"]["eligible_node_count"] == 5


def test_full_node_dependency_replacement_is_visible_through_review_tool(app, planned):
    def proposed(mid, prerequisites=()):
        return ProposedMilestone(
            id=mid, title=f"Deliver {mid}", intent=f"Provide {mid}", scope=["auth.py"],
            dependencies=list(prerequisites),
            dependency_reasons={dep: f"{mid} consumes {dep}" for dep in prerequisites},
            behaviors=[{"key": f"{mid}.contract", "statement": f"{mid} is available"}],
        )

    app.graph.upsert(planned.id, proposed("replacement"), create=True)
    app.graph.upsert(planned.id, proposed("delivery", ["M01"]), create=True)
    app.graph.dependency(planned.id, "M01", "delivery", "Needs the old API", "verification")
    before = app.db.get(planned.id).model_copy(deep=True)
    replacement = proposed("delivery", ["replacement"])
    replacement.title = "Deliver replacement interface"
    replacement.intent = "Provide the replacement's exact saved intent"
    replacement.scope = ["new.py", "compatibility.py"]
    replacement.behaviors[0].statement = "The replacement interface is available"
    app.graph.upsert(planned.id, replacement, create=False)
    saved = app.db.get(planned.id)
    snapshot = saved.model_dump()
    spec = tools()["review_design"]

    report = spec.handler(ToolContext(planned.id, app, before_snapshot=before), spec.parameters())

    result = report["change_context"]
    changes = {edge["prerequisite_id"]: edge for edge in result["dependency_changes"]}
    assert set(changes) == {"M01", "replacement"}
    assert changes["M01"]["current"] is None
    assert changes["M01"]["before"]["kind"] == "verification"
    assert changes["M01"]["before"]["reason"] == "Needs the old API"
    assert changes["replacement"]["before"] is None
    assert changes["replacement"]["current"]["reason"] == "delivery consumes replacement"
    assert set(node_map(result)) == {"M01", "replacement", "delivery"}
    delivery = node_map(result)["delivery"]
    assert before_summary(delivery)["title"] == before.milestone("delivery").title
    assert delivery["current"]["title"] == replacement.title
    for name in ("intent", "scope"):
        assert field_value(result, "delivery", name, "before") == getattr(before.milestone("delivery"), name)
        assert field_value(result, "delivery", name) == getattr(replacement, name)
    assert contract_values(result, "delivery")[0]["statement"] == replacement.behaviors[0].statement
    assert contract_values(result, "delivery", "before")[0]["statement"] == "delivery is available"
    assert result["state"] == "saved_before_transitive_reduction"
    assert app.db.get(planned.id).model_dump() == snapshot
    assert before.milestone("delivery").dependencies == ["M01"]


def test_active_revisions_preserve_scope_and_true_owner_without_historical_leakage():
    before = project(milestone("delivery"), milestone("other"))
    historical = BehaviorRevision(
        id="historical-v99", behavior_key="delivery.contract", version=99,
        statement="Unreferenced historical text must not appear", owner="delivery",
    )
    before.behaviors.append(historical)
    current = before.model_copy(deep=True)
    target = BehaviorRevision(
        id="active-v2", behavior_key="delivery.contract", version=2,
        statement="Revised lasting requirement", owner="delivery", supersedes="delivery-v1",
    )
    temporary = BehaviorRevision(
        id="local-v1", behavior_key="delivery.temporary", version=1,
        statement="Temporary migration check", owner="delivery", acceptance_scope="milestone",
    )
    current.behaviors.extend([target, temporary])
    current.milestone("delivery").behavior_revision_ids = [
        target.id, temporary.id, "other-v1", "missing-revision",
    ]

    result = change_context(before, current)
    node = node_map(result)["delivery"]

    assert before_summary(node)["active_behavior_revision_ids"] == ["delivery-v1"]
    assert node["current"]["active_behavior_revision_ids"] == [target.id, temporary.id]
    assert contract_values(result, "delivery", "before") == [before.behaviors[0].model_dump()]
    assert contract_values(result, "delivery") == [target.model_dump(), temporary.model_dump()]
    assert node["current"]["unresolved_behavior_references"] == [
        {"id": "other-v1", "owner": "other"}, {"id": "missing-revision", "owner": None},
    ]
    assert {item["acceptance_scope"] for item in contract_values(result, "delivery")} == {
        "target", "milestone",
    }
    assert b"historical-v99" not in serialized(result)
    assert b"Unreferenced historical text" not in serialized(result)


def test_source_contracts_remain_separate_and_both_snapshots_are_read_only():
    before = project(
        milestone("delivery", ["SRC_api"]),
        source_nodes=[source("SRC_foundation"), source("SRC_api", ["SRC_foundation"])],
    )
    current = before.model_copy(deep=True)
    current.milestone("delivery").dependencies = []
    current.milestone("delivery").dependency_reasons = {}
    current.milestone("delivery").dependency_types = {}
    current.behaviors[0].statement = "Revised delivery requirement"
    old_data, new_data = before.model_dump(), current.model_dump()

    result = change_context(before, current)
    nodes = node_map(result)

    assert set(nodes) == {"delivery", "SRC_api", "SRC_foundation"}
    for node in before.source_milestones:
        summary = nodes[node.id]["current"]
        assert summary["origin"] == "source"
        assert summary["status"] == "IMPLEMENTED"
        assert summary["source_baseline_id"] == "observed-baseline"
        assert summary["source_behavior_keys"] == [item.key for item in node.source_behaviors]
        assert field_value(result, node.id, "source_refs") == node.source_refs
        assert contract_values(result, node.id) == [item.model_dump() for item in node.source_behaviors]
        assert all(row["kind"] == "source_observation" for row in contracts_for(result, node.id))
        assert "active_behavior_revision_ids" not in summary
        assert "before" not in nodes[node.id]
    assert "source_behavior_keys" not in nodes["delivery"]["current"]
    assert "source inferences, not verified acceptance" in result["basis"]
    assert before.model_dump() == old_data
    assert current.model_dump() == new_data
    # Returned nested structures must not share mutable state with either input.
    contracts_for(result, "SRC_api")[0]["current"]["source_refs"].append("not-a-source")
    contracts_for(result, "delivery")[0]["before"]["statement"] = "Changed output only"
    field_row(result, "SRC_api", "source_refs")["current"].append("not-a-node-source")
    nodes["delivery"]["before_overrides"]["prerequisite_ids"].append("not-a-prerequisite")
    assert before.model_dump() == old_data
    assert current.model_dump() == new_data


@pytest.mark.parametrize("kind", ["added", "removed"])
def test_added_and_removed_nodes_have_explicit_absence(kind):
    populated = project(milestone("delivery"))
    empty = populated.model_copy(deep=True)
    empty.milestones = []
    before, current = (empty, populated) if kind == "added" else (populated, empty)

    result = change_context(before, current)
    node = node_map(result)["delivery"]

    assert result["status"] == "changed"
    assert result["milestone_changes"][0]["change"] == kind
    assert node["before" if kind == "added" else "current"] is None
    present_state = "current" if kind == "added" else "before"
    assert node[present_state]["active_behavior_revision_ids"] == ["delivery-v1"]
    assert contract_values(result, "delivery", present_state) == [populated.behaviors[0].model_dump()]
    contract = contracts_for(result, "delivery")[0]
    assert contract["change"] == kind
    assert contract["before" if kind == "added" else "current"] is None
    for row in result["node_fields"]:
        assert row["before_present"] is (kind == "removed")
        assert row["current_present"] is (kind == "added")
        assert ("before" in row) is (kind == "removed")
        assert ("current" in row) is (kind == "added")


@pytest.mark.parametrize("empty", [False, True], ids=["populated", "empty"])
def test_no_turn_start_snapshot_is_distinct_from_a_meaningful_zero_change(empty):
    before = project() if empty else project(milestone("delivery"))
    current = before.model_copy(deep=True)
    current.revision += 1

    absent = change_context(None, current)
    unchanged = change_context(before, current)

    assert absent["status"] == "no_turn_start_snapshot"
    assert absent["before_revision"] is None
    assert unchanged["status"] == "unchanged"
    assert unchanged["before_revision"] == before.revision
    for result in (absent, unchanged):
        assert result["project_revision"] == current.revision
        assert result["milestone_changes"] == []
        assert result["dependency_changes"] == []
        assert result["nodes"] == []
        assert result["contracts"] == []
        assert result["node_fields"] == []
        assert result["coverage"] == {
            "eligible_node_count": 0, "omitted_node_count": 0, "omitted_node_ids": [],
            "unlisted_omitted_node_count": 0, "omitted_milestone_change_count": 0,
            "omitted_dependency_change_count": 0,
            "detail_scope": "included_node_summaries", "omitted_contract_count": 0,
            "omitted_field_count": 0,
        }
        assert result["truncated"] is False
        assert result["current_state_tool"] == "read_project"
        assert "omitted before-state" in result["historical_omission_limit"]
        assert "verdict" not in result


def test_historical_behavior_and_layout_edits_do_not_fabricate_contract_changes():
    before = project(milestone("delivery"))
    current = before.model_copy(deep=True)
    current.revision += 1
    current.milestone("delivery").position = {"x": 12.0, "y": 24.0}
    current.behaviors.append(BehaviorRevision(
        id="unreferenced", behavior_key="delivery.contract", version=2,
        statement="A historical revision not referenced by the node", owner="delivery",
    ))

    result = change_context(before, current)

    assert result["status"] == "unchanged"
    assert result["nodes"] == []
    assert result["milestone_changes"] == []


@pytest.mark.parametrize("previous,current", [
    ({"same": None, "removed": None, "changed": "old"},
     {"same": None, "added": None, "changed": None}),
    ({"missing_now": None}, {}),
    ({}, {"missing_before": None}),
    ({"same": None}, {"same": None}),
    (None, {"present": None}),
    ({"present": None}, None),
])
def test_summary_before_encoding_distinguishes_missing_fields_from_present_null(previous, current):
    old_data, current_data = deepcopy(previous), deepcopy(current)
    row = {"current": current}

    _before_values(row, previous, current)

    assert before_summary(row) == previous
    if previous is not None and current is not None:
        assert "before" not in row
        assert set(row.get("before_absent_fields", [])) == current.keys() - previous.keys()
        assert row.get("before_overrides", {}) == {
            key: value for key, value in previous.items()
            if key not in current or current[key] != value
        }
    else:
        assert row["before"] == previous
    assert previous == old_data
    assert current == current_data


def test_source_replacement_reconstructs_summary_fields_and_exact_contract_kinds():
    before = project(milestone("delivery"))
    current = before.model_copy(deep=True)
    current.milestones = []
    current.source_milestones = [source("delivery")]

    result = change_context(before, current)
    node = node_map(result)["delivery"]
    previous = before_summary(node)

    assert previous["origin"] == "plan"
    assert previous["active_behavior_revision_ids"] == ["delivery-v1"]
    assert previous["unresolved_behavior_references"] == []
    assert "source_behavior_keys" not in previous
    assert node["current"]["origin"] == "source"
    assert node["current"]["source_behavior_keys"] == ["delivery.observed"]
    assert "active_behavior_revision_ids" not in node["current"]
    assert node["before_absent_fields"] == ["source_behavior_keys"]
    rows = {row["kind"]: row for row in contracts_for(result, "delivery")}
    assert rows["behavior_revision"]["before"] == before.behaviors[0].model_dump()
    assert rows["behavior_revision"]["current"] is None
    assert rows["source_observation"]["before"] is None
    assert rows["source_observation"]["current"] == current.source_milestones[0].source_behaviors[0].model_dump()
    assert rows["behavior_revision"]["change"] == "removed"
    assert rows["source_observation"]["change"] == "added"
    refs = field_row(result, "delivery", "source_refs")
    assert refs["before_present"] is True and refs["current_present"] is True
    assert refs["before"] == []
    assert refs["current"] == current.source_milestones[0].source_refs
    unchanged = field_row(result, "delivery", "scope")
    assert unchanged["before_present"] is True and unchanged["current_present"] is True
    assert "before" not in unchanged
    assert field_value(result, "delivery", "scope", "before") == ["delivery.py"]


def test_scope_only_behavior_change_keeps_exact_identity_owner_and_statement():
    before = project(milestone("delivery"))
    current = before.model_copy(deep=True)
    current.behaviors[0].acceptance_scope = "milestone"

    result = change_context(before, current)
    row = contracts_for(result, "delivery")[0]

    assert row == {
        "node_id": "delivery", "kind": "behavior_revision", "key": "delivery.contract",
        "before": before.behaviors[0].model_dump(), "current": current.behaviors[0].model_dump(),
        "change": "updated",
    }
    assert before_summary(node_map(result)["delivery"]) == node_map(result)["delivery"]["current"]
    assert row["before"]["acceptance_scope"] == "target"
    assert row["current"]["acceptance_scope"] == "milestone"
    assert row["before"]["statement"] == row["current"]["statement"]
    assert "Both acceptance scopes require milestone acceptance" in result["basis"]
    assert "only target contributes to the final goal" in result["basis"]


@pytest.mark.parametrize("kind", ["behavior_key", "revision_reference", "source_key"])
def test_duplicate_keys_and_references_preserve_unchanged_exact_multiplicity(kind):
    if kind == "source_key":
        node = source("SRC_api")
        duplicate = node.source_behaviors[0].model_copy(deep=True)
        duplicate.statement = "Another inference saved under the same key"
        node.source_behaviors.extend([duplicate, node.source_behaviors[0].model_copy(deep=True)])
        before = project(source_nodes=[node])
        expected = [record.model_dump() for record in node.source_behaviors]
    else:
        before = project(milestone("delivery"))
        node = before.milestone("delivery")
        if kind == "behavior_key":
            duplicate = before.behaviors[0].model_copy(update={
                "id": "delivery-v2", "version": 2, "statement": "Second active exact record",
            })
            before.behaviors.append(duplicate)
            node.behavior_revision_ids.append(duplicate.id)
            expected = [record.model_dump() for record in before.behaviors]
        else:
            node.behavior_revision_ids.append(node.behavior_revision_ids[0])
            expected = [before.behaviors[0].model_dump(), before.behaviors[0].model_dump()]
    current = before.model_copy(deep=True)
    changed_node = current.source_milestones[0] if kind == "source_key" else current.milestones[0]
    changed_node.title += " revised"

    result = change_context(before, current)
    rows = contracts_for(result, node.id)

    assert len(rows) == len(expected)
    assert sorted(json.dumps(row["current"], sort_keys=True) for row in rows) == sorted(
        json.dumps(record, sort_keys=True) for record in expected
    )
    assert all(row["change"] == "unchanged" and "before" not in row for row in rows)
    assert node_map(result)[node.id]["omitted_contract_count"] == 0
    ref_field = "source_behavior_keys" if kind == "source_key" else "active_behavior_revision_ids"
    assert len(node_map(result)[node.id]["current"][ref_field]) == len(expected)


def test_duplicate_behavior_keys_match_changed_revision_identity_without_losing_records():
    before = project(milestone("delivery"))
    second = before.behaviors[0].model_copy(update={
        "id": "delivery-v2", "version": 2, "statement": "A second active record",
    })
    before.behaviors.append(second)
    before.milestone("delivery").behavior_revision_ids.append(second.id)
    current = before.model_copy(deep=True)
    current.behaviors[1].statement = "The second active record now differs"
    current.milestone("delivery").behavior_revision_ids.reverse()

    result = change_context(before, current)
    rows = contracts_for(result, "delivery")

    assert len(rows) == 2
    changed = next(row for row in rows if row["change"] == "updated")
    unchanged = next(row for row in rows if row["change"] == "unchanged")
    assert changed["before"] == second.model_dump()
    assert changed["current"] == current.behaviors[1].model_dump()
    assert unchanged["current"] == before.behaviors[0].model_dump()
    assert "before" not in unchanged
    assert node_map(result)["delivery"]["omitted_contract_count"] == 0


def test_missing_and_wrong_owner_references_remain_visible_without_borrowing_contracts():
    before = project(milestone("delivery"), milestone("other"))
    before.milestone("delivery").behavior_revision_ids.extend(["other-v1", "missing-revision"])
    current = before.model_copy(deep=True)
    current.milestone("delivery").title += " revised"

    result = change_context(before, current)
    summary = node_map(result)["delivery"]["current"]

    assert summary["unresolved_behavior_references"] == [
        {"id": "other-v1", "owner": "other"}, {"id": "missing-revision", "owner": None},
    ]
    assert before_summary(node_map(result)["delivery"])["unresolved_behavior_references"] == (
        summary["unresolved_behavior_references"]
    )
    assert summary["active_behavior_revision_ids"] == ["delivery-v1"]
    assert contract_values(result, "delivery") == [before.behaviors[0].model_dump()]
    assert set(node_map(result)) == {"delivery"}
    assert node_map(result)["delivery"]["omitted_contract_count"] == 0


def large_graph():
    nodes = [
        milestone(f"M{index:03}", [f"M{index - 1:03}"] if index else ["SRC_api"])
        for index in range(256)
    ]
    return project(
        *nodes, source_nodes=[source("SRC_foundation"), source("SRC_api", ["SRC_foundation"])],
    )


def assert_coverage_is_honest(result, eligible_ids, changed_count, edge_change_count):
    included = [node["id"] for node in result["nodes"]]
    coverage = result["coverage"]
    omitted = coverage["omitted_node_ids"]
    assert len(included) == len(set(included))
    assert len(omitted) == len(set(omitted))
    assert set(included) <= eligible_ids
    assert set(omitted) <= eligible_ids - set(included)
    assert coverage["eligible_node_count"] == len(eligible_ids)
    assert coverage["omitted_node_count"] == len(eligible_ids) - len(included)
    assert coverage["omitted_node_count"] == len(omitted) + coverage["unlisted_omitted_node_count"]
    assert len(result["milestone_changes"]) + coverage["omitted_milestone_change_count"] == (
        changed_count
    )
    assert len(result["dependency_changes"]) + coverage["omitted_dependency_change_count"] == (
        edge_change_count
    )
    assert len(result["nodes"]) <= 16
    assert len(result["milestone_changes"]) <= 32
    assert len(result["dependency_changes"]) <= 32
    assert len(result["contracts"]) <= 128
    assert len(result["node_fields"]) <= 128
    assert coverage["detail_scope"] == "included_node_summaries"
    assert coverage["omitted_contract_count"] == sum(
        node["omitted_contract_count"] for node in result["nodes"]
    )
    assert coverage["omitted_field_count"] == sum(
        len(node["omitted_fields"]) for node in result["nodes"]
    )
    for node in result["nodes"]:
        assert node["omitted_contract_count"] >= 0
        selected_fields = [
            row["field"] for row in result["node_fields"] if row["node_id"] == node["id"]
        ]
        assert len(selected_fields) == len(set(selected_fields))
        assert len(node["omitted_fields"]) == len(set(node["omitted_fields"]))
        assert not set(selected_fields) & set(node["omitted_fields"])
    assert all(row["node_id"] in included for row in result["contracts"] + result["node_fields"])
    assert len(omitted) <= 32
    assert len(serialized(result)) <= 32768


def test_256_planned_plus_source_nodes_have_deterministic_honest_truncation():
    before = large_graph()
    current = before.model_copy(deep=True)
    for node in current.milestones:
        node.title += " revised"
        node.dependency_reasons = {dep: f"Revised {node.id} contract uses {dep}" for dep in node.dependencies}
    saved_before, saved_current = before.model_dump(), current.model_dump()

    result = change_context(before, current)
    reversed_before, reversed_current = before.model_copy(deep=True), current.model_copy(deep=True)
    for snapshot in (reversed_before, reversed_current):
        snapshot.milestones.reverse()
        snapshot.source_milestones.reverse()
        snapshot.behaviors.reverse()

    assert result == change_context(before, current)
    assert result == change_context(reversed_before, reversed_current)
    eligible_ids = {node.id for node in [*before.milestones, *before.source_milestones]}
    assert_coverage_is_honest(result, eligible_ids, changed_count=256, edge_change_count=256)
    assert result["truncated"] is True
    assert result["nodes"]
    assert before.model_dump() == saved_before
    assert current.model_dump() == saved_current


def test_large_graph_single_change_still_samples_both_directions():
    before = large_graph()
    current = before.model_copy(deep=True)
    current.milestone("M128").title = "Revised central contract"

    result = change_context(before, current)

    included = set(node_map(result))
    assert {"M128", "M127", "M129"} <= included
    eligible_ids = {node.id for node in [*before.milestones, *before.source_milestones]}
    assert_coverage_is_honest(result, eligible_ids, changed_count=1, edge_change_count=0)
    assert len(result["nodes"]) == 16


def test_many_long_edge_deltas_leave_room_for_whole_changed_contracts():
    before = project(
        milestone("foundation"),
        *(milestone(f"M{index:03}", ["foundation"]) for index in range(40)),
    )
    old_reason = "Old prerequisite contract: " + "a" * 1000
    new_reason = "New prerequisite contract: " + "b" * 1000
    for node in before.milestones[1:]:
        node.dependency_reasons["foundation"] = old_reason
    current = before.model_copy(deep=True)
    for node in current.milestones[1:]:
        node.dependency_reasons["foundation"] = new_reason

    result = change_context(before, current)

    assert_coverage_is_honest(
        result, {node.id for node in before.milestones},
        changed_count=40, edge_change_count=40,
    )
    assert result["coverage"]["omitted_milestone_change_count"] > 0
    assert result["coverage"]["omitted_dependency_change_count"] > 0
    changed_nodes = [node for node in result["nodes"] if node["id"] != "foundation"]
    assert changed_nodes, "Edge-delta descriptions must not crowd out all changed summaries"
    for node in changed_nodes:
        assert node["current"]["prerequisite_ids"] == ["foundation"]
        assert contract_values(result, node["id"]), "Preserve a whole contract for each owner"
    prerequisites = [row for row in result["node_fields"] if row["field"] == "prerequisites"]
    assert prerequisites, "Edge-delta descriptions must leave room for some exact changed fields"
    for row in prerequisites:
        assert row["before"][0]["reason"] == old_reason
        assert row["current"][0]["reason"] == new_reason
    for node in changed_nodes:
        assert any(row["node_id"] == node["id"] for row in prerequisites) or (
            "prerequisites" in node["omitted_fields"]
        )
    for edge in result["dependency_changes"]:
        assert edge["before"]["reason"] == old_reason
        assert edge["current"]["reason"] == new_reason
    assert result["truncated"] is True


@pytest.mark.parametrize("oversized", ["contract", "behavior", "edge_reason", "legacy_id"])
def test_pathological_whole_records_are_omitted_without_fake_shortened_content(oversized):
    huge = "完整记录🧪" * 12000
    large_id = huge if oversized == "legacy_id" else "large"
    before = project(milestone("small"), milestone(large_id, ["small"]))
    current = before.model_copy(deep=True)
    changed = current.milestone(large_id)
    if oversized == "contract":
        changed.intent = huge
    elif oversized == "behavior":
        current.behaviors[1].statement = huge
    elif oversized == "edge_reason":
        changed.dependency_reasons["small"] = huge
    else:
        changed.title = "Revised legacy node"

    result = change_context(before, current)

    assert_coverage_is_honest(
        result, {"small", large_id}, changed_count=1,
        edge_change_count=1 if oversized == "edge_reason" else 0,
    )
    assert result["truncated"] is True
    nodes = node_map(result)
    if oversized == "legacy_id":
        assert result["coverage"]["omitted_node_count"] == 1
        assert set(nodes) == {"small"}
    else:
        assert result["coverage"]["omitted_node_count"] == 0
        assert set(nodes) == {"small", "large"}
        if oversized == "behavior":
            assert nodes["large"]["omitted_contract_count"] == 1
            assert contracts_for(result, "large") == []
        else:
            omitted_field = "intent" if oversized == "contract" else "prerequisites"
            assert omitted_field in nodes["large"]["omitted_fields"]
            assert not any(
                row["node_id"] == "large" and row["field"] == omitted_field
                for row in result["node_fields"]
            )
    assert field_value(result, "small", "intent") == "Provide the saved small contract"
    assert contract_values(result, "small") == [before.behaviors[0].model_dump()]
    assert huge.encode("utf-8") not in serialized(result)
    assert "完整记录" not in serialized(result).decode("utf-8")


def test_large_graph_projection_finishes_with_a_generous_cpu_budget():
    before = large_graph()
    current = before.model_copy(deep=True)
    current.milestone("M128").title = "Revised central contract"

    start = perf_counter()
    result = change_context(before, current)
    elapsed = perf_counter() - start

    assert result["coverage"]["eligible_node_count"] == 258
    # A 5-second bound is intentionally much looser than normal local latency;
    # it catches accidental combinatorial traversal, not noisy CI scheduling.
    assert elapsed < 5.0, f"258-node change projection took {elapsed:.3f}s"


def test_long_scope_does_not_evict_neighboring_summaries_or_silently_shorten_details():
    before = project(*(milestone(f"M{index:02}", [f"M{index - 1:02}"] if index else [])
                       for index in range(16)))
    before.milestones[0].scope = ["preserve-the-whole-scope/" + "界" * 20000]
    current = before.model_copy(deep=True)
    current.milestones[0].title += " revised"

    result = change_context(before, current)

    assert set(node_map(result)) == {node.id for node in before.milestones}
    assert_coverage_is_honest(result, set(node_map(result)), 1, 0)
    assert "scope" in node_map(result)["M00"]["omitted_fields"]
    assert not any(row["node_id"] == "M00" and row["field"] == "scope"
                   for row in result["node_fields"])
    assert b"preserve-the-whole-scope/" not in serialized(result)
    assert all(contracts_for(result, mid) for mid in node_map(result))
    assert result["truncated"] is True


def test_changed_behavior_details_are_allocated_fairly_across_owners():
    before = project(milestone("a"), milestone("b", ["a"]), milestone("c", ["b"]))
    for node in before.milestones:
        for index in range(6 if node.id == "a" else 2):
            record = BehaviorRevision(
                id=f"{node.id}-{index}", behavior_key=f"{node.id}.{index}", version=1,
                owner=node.id, statement=f"Exact {node.id}/{index} contract " + "a" * 1200,
            )
            before.behaviors.append(record)
            node.behavior_revision_ids.append(record.id)
    current = before.model_copy(deep=True)
    for record in current.behaviors:
        if record.id.endswith("-v1"):
            continue
        record.statement += " now revised"

    result = change_context(before, current)

    assert [row["node_id"] for row in result["contracts"][:3]] == ["a", "b", "c"]
    assert all(row["change"] == "updated" for row in result["contracts"][:3])
    for owner in ("b", "c"):
        changed = [row for row in contracts_for(result, owner) if row["change"] == "updated"]
        assert len(changed) == 2
    for row in result["contracts"]:
        if row["change"] == "updated":
            assert row["current"] == next(record.model_dump() for record in current.behaviors
                                          if record.id == row["current"]["id"])
            assert row["before"] == next(record.model_dump() for record in before.behaviors
                                         if record.id == row["before"]["id"])
    assert_coverage_is_honest(result, {"a", "b", "c"}, 3, 0)


def test_changed_scope_and_intent_precede_additional_unchanged_contract_details():
    before = project(milestone("delivery"), milestone("neighbor", ["delivery"]))
    for index in range(24):
        record = BehaviorRevision(
            id=f"delivery-extra-{index}", behavior_key=f"delivery.extra.{index}", version=1,
            owner="delivery", statement="Exact unchanged contract " + "x" * 1600,
        )
        before.behaviors.append(record)
        before.milestone("delivery").behavior_revision_ids.append(record.id)
    current = before.model_copy(deep=True)
    current.milestone("delivery").scope = ["new-scope/" + "s" * 4000]
    current.milestone("delivery").intent = "Exact revised intent " + "i" * 1500

    result = change_context(before, current)

    for name in ("scope", "intent"):
        assert field_value(result, "delivery", name) == getattr(current.milestone("delivery"), name)
        assert field_value(result, "delivery", name, "before") == getattr(before.milestone("delivery"), name)
    assert contracts_for(result, "neighbor")
    node = node_map(result)["delivery"]
    assert node["omitted_contract_count"] > 0
    assert len(contracts_for(result, "delivery")) + node["omitted_contract_count"] == 25
    assert node["current"]["active_behavior_revision_ids"] == before.milestone("delivery").behavior_revision_ids
    assert_coverage_is_honest(result, {"delivery", "neighbor"}, 1, 0)


def test_contract_detail_cap_reports_omissions_without_losing_stable_references():
    before = project(milestone("a"))
    before.behaviors = [
        BehaviorRevision(id=str(index), behavior_key=str(index), version=1, statement="x", owner="a")
        for index in range(140)
    ]
    before.milestones[0].behavior_revision_ids = [record.id for record in before.behaviors]
    current = before.model_copy(deep=True)
    current.milestones[0].title += " revised"

    result = change_context(before, current)
    node = node_map(result)["a"]

    assert len(result["contracts"]) <= 128
    assert node["omitted_contract_count"] > 0
    assert len(result["contracts"]) + node["omitted_contract_count"] == 140
    assert node["current"]["active_behavior_revision_ids"] == before.milestones[0].behavior_revision_ids
    assert before_summary(node)["active_behavior_revision_ids"] == before.milestones[0].behavior_revision_ids
    assert_coverage_is_honest(result, {"a"}, 1, 0)
