"""Provider-visible guidance contracts, not live generation or architecture-quality scores."""

import asyncio
import json

from evograph.agent_tools import tools
from evograph.application.design_workflow import DESIGN_WORKFLOW


def test_architecture_guidance_reaches_normal_agent_turn(app):
    project = app.projects.create("Small workshop")
    captured = []

    async def stream(messages, schemas):
        captured.append((messages, schemas))
        yield {"type": "text", "text": "Fixture only: no design generated."}

    app.settings.stream = stream

    async def run():
        return [event async for event in app.agent.stream(project.id, "设计一家维修店的预约工具")]

    events = asyncio.run(run())
    system = captured[0][0][0]["content"]
    assert DESIGN_WORKFLOW in system
    normalized_system = " ".join(system.split())
    for guidance in (
        "Patterns are optional means, not a quality checklist",
        "logical groups do not imply separate services",
        "do not split services just",
        "one plausible change against the boundaries",
        "Omitted decisions, quality_scenarios, risks and research_ids retain",
        "A caveat in risks does not justify stating the same uncertain premise as a fact",
        "A later milestone cannot retroactively make an earlier contract implementable or safe",
        "A timeout can mean the remote action succeeded but its reply was lost",
        "trade an explicit no-duplicates requirement for at-least-once retry without the user's agreement",
        "Disclosing an exception, calling it a default, or inviting the user to object later is not agreement",
        "old unscoped binaries must not serve newly separated data",
        "Recovery claims must name the compatible backup artifacts",
        "Examples in this playbook are not additional user requirements",
    ):
        assert guidance in normalized_system
    assert not [event for event in events if event["type"] == "error"]
    assert not app.db.get(project.id).architectures


def test_tool_schema_explains_evolution_semantics_without_new_required_checkboxes():
    schema = tools()["update_architecture"].schema()["function"]
    fields = schema["parameters"]["properties"]
    assert set(schema["parameters"]["required"]) == {"summary", "technologies", "diagram"}
    for name in ("decisions", "quality_scenarios", "risks", "research_ids"):
        assert "Omit to retain" in fields[name]["description"]
        assert "[] to clear" in fields[name]["description"]
    assert "Patterns are optional" in fields["decisions"]["description"]
    assert "logical groups do not imply" in schema["description"]
    assert "growth alone does not justify" in schema["description"]


def test_post_edit_reminder_checks_guarantees_against_risks(app):
    project = app.projects.create("Review reminder contract")
    captured = []

    async def stream(messages, schemas):
        # Capture only immutable text because the runtime appends to messages.
        captured.append([message.get("content") for message in messages])
        if len(captured) == 1:
            yield {
                "type": "tool_delta", "index": 0, "id": "fixture",
                "name": "update_architecture",
                "arguments": json.dumps({
                    "summary": "Fixture only; no generated design quality claim",
                    "technologies": [{"area": "Runtime", "choice": "Existing",
                                      "rationale": "Fixture"}],
                    "diagram": {"id": "system", "title": "Fixture",
                                "nodes": [{"id": "app", "label": "App"}]},
                }),
            }
        else:
            yield {"type": "text", "text": "Fixture response"}

    app.settings.stream = stream

    async def run():
        return [event async for event in app.agent.stream(project.id, "请设计预约工具架构")]

    events = asyncio.run(run())
    assert not [event for event in events if event["type"] in {"error", "tool_failed"}]
    assert len(captured) == 3
    reminder = captured[2][-1]
    assert "Compare the user's hard requirements" in reminder
    assert "A risk caveat cannot weaken a promised guarantee" in reminder
    assert "local deduplication is not proof of a remote effect occurring once" in reminder
    assert "Do not describe unresolved contradictions as completed work" in reminder
    assert "claims against prospective_target_membership" in reminder
    assert "Remove unrequested product extensions" in reminder
    assert "Keep the user's requirement fixed while repairing the design" in reminder
