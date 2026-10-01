"""Saved project assets and message references have separate durable contracts."""

import asyncio
import base64
import json
from copy import deepcopy

import pytest
from conftest import MemorySecrets
from evograph.application.api import Application


def upload(app, project_id, index):
    return app.attachments.upload(
        project_id,
        f"reference-{index}.txt",
        base64.b64encode(f"unique-reference-body-{index}".encode()).decode(),
    )


def test_seven_saved_assets_survive_reload_but_only_six_can_be_referenced(app):
    project = app.projects.create("Attachment outcomes")
    assets = [upload(app, project.id, index) for index in range(7)]
    reloaded = Application(app.db.path.parent, MemorySecrets())

    assert [asset.id for asset in reloaded.db.get(project.id).attachments] == [
        asset.id for asset in assets
    ]
    selected = [asset.id for asset in assets[:6]]
    blocks = reloaded.attachments.context(project.id, selected)
    assert len(blocks) == 6
    assert all(f"id={asset.id}," in block["text"] for asset, block in zip(assets, blocks))
    assert all(assets[6].excerpt not in block["text"] for block in blocks)
    with pytest.raises(ValueError, match="最多引用 6"):
        reloaded.attachments.context(project.id, [asset.id for asset in assets])
    assert len(reloaded.db.get(project.id).attachments) == 7


def test_later_rejected_file_does_not_undo_confirmed_saved_assets(app):
    project = app.projects.create("Partial attachment upload")
    first = upload(app, project.id, 1)
    revision = app.db.get(project.id).revision

    with pytest.raises(ValueError, match="无法读取文件"):
        app.attachments.upload(project.id, "broken.png", base64.b64encode(b"bad image").decode())

    reloaded = Application(app.db.path.parent, MemorySecrets())
    assert [asset.id for asset in reloaded.db.get(project.id).attachments] == [first.id]
    assert reloaded.db.get(project.id).revision == revision
    assert first.excerpt in reloaded.attachments.context(project.id, [first.id])[0]["text"]
    assert upload(reloaded, project.id, 1).id == first.id
    assert reloaded.db.get(project.id).revision == revision


def test_duplicate_reuses_canonical_asset_even_at_project_limit(app):
    project = app.projects.create("Full attachment library")
    assets = [upload(app, project.id, index) for index in range(40)]
    before = app.db.get(project.id)

    reused = app.attachments.upload(
        project.id,
        "renamed-copy.txt",
        base64.b64encode(assets[0].excerpt.encode()).decode(),
    )
    assert reused.id == assets[0].id
    assert reused.name == assets[0].name
    assert app.db.get(project.id).revision == before.revision
    with pytest.raises(ValueError, match="40 份上限"):
        upload(app, project.id, 40)
    assert app.db.get(project.id).attachments == before.attachments


def test_provider_receives_only_selected_reference_contents(app):
    project = app.projects.create("Selected reference context")
    assets = [upload(app, project.id, index) for index in range(7)]
    selected = [assets[index] for index in [6, 2, 0, 4, 3, 1]]
    requests = []

    async def provider(messages, schemas):
        requests.append(deepcopy(messages))
        yield {
            "type": "tool_delta",
            "index": 0,
            "id": "attachment-question",
            "name": "ask_user",
            "arguments": json.dumps(
                {
                    "prompt": "Which reference should define the first slice?",
                    "category": "missing_design_input",
                }
            ),
        }

    app.settings.stream = provider

    async def run():
        return [
            event
            async for event in app.agent.stream(
                project.id, "Use these references", attachment_ids=[asset.id for asset in selected]
            )
        ]

    events = asyncio.run(run())
    assert len(requests) == 1
    user_content = requests[0][-1]["content"]
    assert user_content[0]["text"] == "Use these references"
    assert len(user_content[1:]) == 6
    for asset, block in zip(selected, user_content[1:]):
        assert f"id={asset.id}," in block["text"]
        assert asset.excerpt in block["text"]
    assert assets[5].excerpt not in json.dumps(requests)
    assert events[-1]["summary"]["status"] == "waiting"
    assert len(app.db.get(project.id).attachments) == 7
