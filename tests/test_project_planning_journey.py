"""Deterministic provider-tool journey; validates contracts, not live model quality.

All planning edits go through the production composer/stream/tool path. The
scripted provider supplies proposed deliverables, never implementation evidence.
"""

import json
from copy import deepcopy

import pytest
from conftest import MemorySecrets
from evograph.application.api import Application
from evograph.application.design_workflow import ARCHITECTURE_INTENT
from evograph.domain.composer import ComposerDocument
from evograph.transport.desktop import DesktopBridge
from evograph.transport.http import create_app
from fastapi.testclient import TestClient


def milestone(mid, title, path, key, statement, component, dependencies=None):
    dependencies = dependencies or {}
    return {
        "id": mid,
        "title": title,
        "intent": f"Deliver a separately mergeable contract: {statement}",
        "scope": [path],
        "resources": [f"module:{component}"],
        "architecture_components": [component] if component else [],
        "dependencies": list(dependencies),
        "dependency_reasons": dependencies,
        "behaviors": [{"key": key, "statement": statement, "acceptance_scope": "target"}],
    }


def architecture(*, on_demand=False):
    nodes = [
        {
            "id": "capture",
            "label": "Feed capture",
            "description": "Own stable feed item IDs and the catalog read contract",
        },
        {
            "id": "digest",
            "label": "On-demand digest" if on_demand else "Daily digest worker",
            "description": "Build per-topic requested digests"
            if on_demand
            else "Build daily digests",
        },
        {
            "id": "ui",
            "label": "Digest interface",
            "role": "frontend",
            "description": "Display digest results and recoverable request failures",
        },
    ]
    edges = [
        {"source": "digest", "target": "capture", "label": "read catalog items"},
        {"source": "ui", "target": "digest", "label": "request digest"},
    ]
    if on_demand:
        nodes.append(
            {
                "id": "export",
                "label": "CSV export",
                "description": "Serialize digest rows; export audience requires a user decision",
            }
        )
        edges.extend(
            [
                {"source": "export", "target": "digest", "label": "read digest rows"},
                {"source": "ui", "target": "export", "label": "download CSV"},
            ]
        )
    return {
        "summary": "On-demand topic digests and CSV exports" if on_demand else "Daily feed digests",
        "technologies": [
            {
                "area": "runtime",
                "choice": "Python",
                "rationale": "User's supplied prototype constraint",
            }
        ],
        "decisions": ["Keep capture, digest and export as modules in one local application"],
        "quality_scenarios": [
            {
                "concern": "reliability",
                "scenario": "A repeated request uses the same catalog item IDs",
                "measure": "Proposed acceptance: no duplicate rows for identical item IDs",
                "approach": "Deduplicate by stable catalog identity",
            }
        ],
        "risks": ["No source or acceptance evidence yet; this is a proposed implementation"],
        "diagram": {
            "id": "feed-system",
            "title": "Feed digest modules",
            "nodes": nodes,
            "edges": edges,
            "groups": [
                {"id": "client", "label": "Client", "kind": "client", "member_node_ids": ["ui"]},
                {
                    "id": "server",
                    "label": "Local service",
                    "kind": "server",
                    "member_node_ids": [node["id"] for node in nodes if node["id"] != "ui"],
                },
            ],
        },
    }


def script_provider(app, calls):
    pending = iter(calls)
    requests = []

    async def stream(messages, schemas):
        requests.append(deepcopy(messages))
        call = next(pending, None)
        if call is None:
            yield {"type": "text", "text": "规划已保存；未实现代码，也没有执行验收。"}
            return
        name, arguments = call
        assert name in {schema["function"]["name"] for schema in schemas}
        serialized = json.dumps(arguments, ensure_ascii=False)
        # Exercise real incremental tool assembly, not direct service seeding.
        for offset in range(0, len(serialized), 37):
            yield {
                "type": "tool_delta",
                "index": 0,
                "id": f"call-{len(requests)}" if offset == 0 else "",
                "name": name if offset == 0 else "",
                "arguments": serialized[offset : offset + 37],
            }

    app.settings.stream = stream
    return requests


def compose(app, project_id, content, *, document=None, question_id=None):
    request = {"project_id": project_id, "content": content}
    if document is not None:
        request["composer_document"] = document
    if question_id is not None:
        request["question_id"] = question_id
    with TestClient(create_app(app)) as client:
        response = client.post("/api/agent/stream", json=request)
    assert response.status_code == 200
    return [json.loads(line) for line in response.text.splitlines()]


def receipt(app, project_id, events, before, status="completed"):
    assert not [event for event in events if event["type"] in {"error", "tool_failed"}], events
    done = events[-1]
    assert done["type"] == "done"
    summary = done["summary"]
    assert summary["status"] == status
    assert summary["before_revision"] == before.revision
    after = app.db.get(project_id)
    assert summary["after_revision"] == after.revision - 1
    assert done["project"] == app.projects.get(project_id)
    saved = [
        json.loads(event["detail"])
        for event in app.db.events(project_id)
        if event["kind"] == "agent_turn_finished"
    ]
    assert summary in saved
    assert app.agent.turn_result(project_id, summary["turn_id"]) == {
        "turn_id": summary["turn_id"],
        "pending": False,
        "summary": summary,
    }
    assert_no_acceptance(after, done["project"])
    for event in events:
        if event.get("project"):
            assert event["project"]["evidence"] == []
            assert event["project"]["acceptance"]["passed"] == 0
            assert event["project"]["acceptance"]["achieved"] is False
    return after, summary["changes"], summary


def assert_no_acceptance(project, view):
    assert project.repository == ""
    assert project.baselines == project.evidence == project.acceptance_requests == []
    assert all(node.status == "PLANNED" and not node.lease_active for node in project.milestones)
    assert view["acceptance"] == {
        "passed": 0,
        "total": len(project.targets[-1].required_behavior_ids) if project.targets else 0,
        "achieved": False,
    }
    assert view["verified_behaviors"] == []


@pytest.mark.parametrize(
    "typed_reference", [False, True], ids=["plain-composer", "optional-reference"]
)
def test_repository_free_project_can_plan_reopen_revise_and_answer_in_one_composer(
    app, typed_reference
):
    description = "A local Python feed digest prototype. Start with daily summaries."
    created = DesktopBridge(app).command(
        "projects.create_with_outcome",
        {"name": "Feed desk", "description": description, "request_id": "feed-journey"},
    )
    assert created["ok"] and created["data"]["outcome"] == "created"
    pid = created["data"]["project"]["id"]
    fresh = app.db.get(pid)
    assert fresh.description == description and fresh.repository == ""
    assert fresh.milestones == fresh.architectures == []
    assert app.db.messages(pid) == []  # Saving the description never auto-sends an idea.
    capture = milestone(
        "M01",
        "Persist feed item identities",
        "backend/catalog.py",
        "catalog.capture",
        "Importing an item twice retains one item with a stable ID",
        "capture",
    )
    digest = milestone(
        "M02",
        "Build daily digests",
        "backend/digest.py",
        "digest.generate",
        "The daily worker produces one digest containing the captured items",
        "digest",
        {"M01": "Digest generation consumes the stable catalog item contract"},
    )
    interface = milestone(
        "M03",
        "Show digest results",
        "frontend/digests.vue",
        "digest.display",
        "The interface displays the daily digest and its item links",
        "ui",
        {"M02": "The interface consumes the generated digest response"},
    )
    initial_target = "Capture feeds and display a daily digest locally"
    requests = script_provider(
        app,
        [
            ("read_project", {}),
            ("update_architecture", architecture()),
            *(("create_milestone", node) for node in (capture, digest, interface)),
            ("set_target", {"statement": initial_target}),
            ("review_design", {}),
        ],
    )
    idea = "Plan my feed digest idea as three separately mergeable PRs and a module architecture."
    first, changes, first_receipt = receipt(app, pid, compose(app, pid, idea), fresh)
    initial_state = json.loads(requests[0][0]["content"].split("Current state (data):\n", 1)[1])
    assert initial_state["description"] == description and initial_state["milestones"] == []
    assert requests[0][-1] == {"role": "user", "content": idea}
    assert [node.id for node in first.milestones] == ["M01", "M02", "M03"]
    assert [item["id"] for item in changes["milestones"]["added"]] == ["M01", "M02", "M03"]
    assert changes["milestones"]["updated"] == changes["milestones"]["removed"] == []
    assert changes["architecture"] == {"before_revision": None, "after_revision": 1}
    assert first.targets[-1].statement == initial_target
    assert changes["target"]["required_behavior_ids"]["added"] == sorted(
        first.targets[-1].required_behavior_ids
    )

    # A new application instance reads the persisted aggregate, messages and receipt.
    reopened = Application(app.db.path.parent, MemorySecrets())
    assert reopened.db.get(pid) == first
    assert reopened.projects.get(pid) == app.projects.get(pid)
    assert reopened.agent.turn_result(pid, first_receipt["turn_id"])["summary"] == first_receipt
    revised_digest = deepcopy(digest)
    revised_digest["title"] = "Generate on-demand topic digests"
    revised_digest["intent"] = "Replace the schedule with a requested topic filter"
    revised_digest["behaviors"][0]["statement"] = "A request returns only items matching its topic"
    revised_ui = deepcopy(interface)
    revised_ui["intent"] = "Submit a topic and display results with an export action"
    revised_ui["behaviors"][0]["statement"] = "The interface requests a topic digest and offers CSV"
    export = milestone(
        "M04",
        "Serialize digest CSV",
        "backend/export.py",
        "digest.export",
        "Digest rows serialize to CSV with stable item ID and title columns",
        "export",
        {"M02": "CSV serialization consumes the on-demand digest row contract"},
    )
    # This is a local delivery check until the export audience is decided.
    export["behaviors"][0]["acceptance_scope"] = "milestone"
    revised_target = "Request a topic digest locally and download its CSV"
    followup = "Change to on-demand topic filtering and add CSV export; update affected UI and dependencies."
    doc = None
    if typed_reference:
        doc = {
            "version": 1,
            "parts": [
                {"type": "text", "text": followup + " Context: "},
                {
                    "type": "reference",
                    "kind": "milestone",
                    "project_id": pid,
                    "id": "M02",
                    "label": first.milestone("M02").title,
                },
            ],
        }
        followup = ComposerDocument.model_validate(doc).plain_text()
    requests = script_provider(
        reopened,
        [
            ("read_project", {}),
            ("update_architecture", architecture(on_demand=True)),
            ("update_milestone", revised_digest),
            ("update_milestone", revised_ui),
            ("create_milestone", export),
            ("remove_dependency", {"source": "M02", "target": "M03"}),
            (
                "add_dependency",
                {
                    "source": "M04",
                    "target": "M03",
                    "reason": "The export action consumes the CSV serializer contract",
                },
            ),
            ("set_target", {"statement": revised_target}),
            ("review_design", {}),
        ],
    )
    revised, changes, revision_receipt = receipt(
        reopened, pid, compose(reopened, pid, followup, document=doc), first
    )
    assert [node.id for node in revised.milestones] == ["M01", "M02", "M03", "M04"]
    assert revised.behaviors[: len(first.behaviors)] == first.behaviors
    assert (
        revised.milestone("M01").behavior_revision_ids
        == first.milestone("M01").behavior_revision_ids
    )
    for mid in ("M02", "M03"):
        assert (
            revised.milestone(mid).behavior_revision_ids
            != first.milestone(mid).behavior_revision_ids
        )
    assert revised.architectures[0] == first.architectures[0]
    current_components = {node.id for node in revised.architectures[-1].diagram.nodes}
    for node in revised.milestones:
        assert node.architecture_revision == 2
        assert set(node.architecture_components) <= current_components
    assert revised.milestone("M03").dependencies == ["M04"]
    assert revised.milestone("M04").dependencies == ["M02"]
    assert changes["milestones"]["added"] == [{"id": "M04", "title": export["title"], "fields": []}]
    updates = {item["id"]: item["fields"] for item in changes["milestones"]["updated"]}
    assert {"intent", "behavior_revision_ids"} <= set(updates["M03"])
    assert changes["milestones"]["removed"] == []
    assert {(edge["source"], edge["target"]) for edge in changes["dependencies"]["added"]} == {
        ("M02", "M04"),
        ("M04", "M03"),
    }
    assert changes["dependencies"]["removed"] == [
        {
            "source": "M02",
            "target": "M03",
            "type": "implementation",
            "reason": interface["dependency_reasons"]["M02"],
        }
    ]
    assert changes["architecture"] == {"before_revision": 1, "after_revision": 2}
    assert revised.targets[-1].statement == revised_target
    assert changes["target"]["required_behavior_ids"] == {
        "added": sorted(
            set(revised.targets[-1].required_behavior_ids)
            - set(first.targets[-1].required_behavior_ids)
        ),
        "removed": sorted(
            set(first.targets[-1].required_behavior_ids)
            - set(revised.targets[-1].required_behavior_ids)
        ),
    }
    assert not set(revised.milestone("M04").behavior_revision_ids) & set(
        revised.targets[-1].required_behavior_ids
    )
    if typed_reference:
        blocks = requests[0][-1]["content"]
        assert blocks[0]["text"] == followup
        references = json.loads(blocks[1]["text"].split("\n", 1)[1])
        assert [(ref["kind"], ref["id"]) for ref in references] == [("milestone", "M02")]
        assert "not an edit scope" in blocks[1]["text"]
        assert "M03" in updates  # Unreferenced dependent still changes through the same stream.
    else:
        assert requests[0][-1] == {"role": "user", "content": followup}
    assert [message for message in reopened.db.messages(pid) if message["role"] == "user"][-1][
        "composer_document"
    ] == doc

    script_provider(
        reopened,
        [
            (
                "ask_user",
                {
                    "prompt": "Who may download an exported digest?",
                    "category": "decision",
                    "options": ["Only me", "Anyone with the link"],
                    "context": "A public link would change the export access boundary",
                },
            )
        ],
    )
    waiting, changes, question_receipt = receipt(
        reopened,
        pid,
        compose(reopened, pid, "Clarify the export audience before finalizing it"),
        revised,
        status="waiting",
    )
    question = waiting.question
    assert question and not question_receipt["changed"]
    reopened = Application(app.db.path.parent, MemorySecrets())
    assert reopened.db.get(pid).question == question
    before_messages = reopened.db.messages(pid)
    answered_export = deepcopy(export)
    answered_export["behaviors"][0].update(
        acceptance_scope="target",
        statement="Only the local owner can download a CSV containing stable item IDs and titles",
    )
    requests = script_provider(
        reopened, [("update_milestone", answered_export), ("review_design", {})]
    )
    answer_doc = {"version": 1, "parts": [{"type": "text", "text": "Only me"}]}
    # An expired question cannot consume this answer or redirect its provenance.
    rejected = compose(reopened, pid, "Only me", document=answer_doc, question_id="older-question")
    assert any(event["type"] == "error" for event in rejected)
    assert requests == [] and reopened.db.messages(pid) == before_messages
    assert reopened.db.get(pid).question == question
    before_answer = reopened.db.get(pid)
    answered, changes, answer_receipt = receipt(
        reopened,
        pid,
        compose(reopened, pid, "Only me", document=answer_doc, question_id=question.id),
        before_answer,
    )
    assert answered.question is None
    assert question.prompt in [
        message.get("content") for message in requests[0] if message["role"] == "assistant"
    ]
    assert requests[0][-1] == {"role": "user", "content": "Only me"}
    user_messages = [message for message in reopened.db.messages(pid) if message["role"] == "user"]
    assert user_messages[-1]["content"] == "Only me"
    assert user_messages[-1]["composer_document"] == answer_doc
    assert len(user_messages) == 4
    answer_events = [event for event in reopened.db.events(pid) if event["kind"] == "agent_answer"]
    assert len(answer_events) == 1 and answer_events[0]["detail"] == "Only me"
    assert changes["target"]["required_behavior_ids"] == {
        "added": answered.milestone("M04").behavior_revision_ids,
        "removed": [],
    }
    assert changes["target"]["required_behavior_changes"] == [
        {
            "behavior_key": "digest.export",
            "before_id": None,
            "after_id": answered.milestone("M04").behavior_revision_ids[0],
            "fields": ["statement", "acceptance_scope"],
        }
    ]
    assert changes["architecture"] is None
    for saved_receipt in (first_receipt, revision_receipt, question_receipt, answer_receipt):
        assert reopened.agent.turn_result(pid, saved_receipt["turn_id"])["summary"] == saved_receipt


def test_repository_free_roadmap_only_composer_does_not_require_architecture(app):
    project = app.projects.create("Roadmap only", "A small local capture tool")
    node = milestone(
        "M01",
        "Persist feed item identities",
        "backend/catalog.py",
        "catalog.capture",
        "Repeated imports retain the same item ID",
        "",
    )
    requests = script_provider(app, [("create_milestone", node), ("review_design", {})])
    content = "Only plan the PR roadmap. Defer architecture and diagrams."
    saved, changes, summary = receipt(app, project.id, compose(app, project.id, content), project)
    assert ARCHITECTURE_INTENT in requests[0][0]["content"]
    assert requests[0][-1] == {"role": "user", "content": content}
    assert saved.milestones[0].architecture_components == []
    assert saved.architectures == saved.diagrams == saved.uml_diagrams == []
    assert changes["architecture"] is None and summary["changed"]
    assert saved.question is None
