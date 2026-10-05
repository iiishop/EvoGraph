"""Read a frozen snapshot into a temporary DB; capture router admission, never dispatch."""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from evograph.application.api import Application  # noqa: E402
from evograph.application.plan_jobs import boundary_kind  # noqa: E402
from evograph.application.plan_units import schedule_findings  # noqa: E402
from evograph.domain.models import Project  # noqa: E402
from evograph.infrastructure.plan_jobs import start_pins  # noqa: E402


class NoSecrets:
    def get(self, name):
        raise AssertionError("Offline preflight must never read credentials")

    def set(self, name, secret):
        raise AssertionError("Offline preflight must never write credentials")


def preflight(frozen_dir):
    files = {name: (frozen_dir / name).read_bytes() for name in ("project.json", "candidates.json", "events.json")}
    original = json.loads(files["candidates.json"])
    old = original[-1]
    before = Project.model_validate_json(files["project.json"])
    with TemporaryDirectory(prefix="evograph-job-preflight-") as directory:
        app = Application(Path(directory), NoSecrets())
        app.db.create(before)
        with app.db.connect() as c:
            for record in original:
                c.execute("INSERT INTO plan_candidates VALUES(?,?,?,?)", (record["id"], before.id,
                          json.dumps(record, ensure_ascii=False), record["created_at"]))
            for event in json.loads(files["events.json"]):
                c.execute("INSERT INTO events VALUES(?,?,?,?,?)", (event["id"], before.id, event["kind"], event["detail"], event["created_at"]))
        async def forbidden_provider(*args, **kwargs):
            raise AssertionError("No provider call is authorized in this preflight")
            yield  # pragma: no cover
        app.settings.stream = forbidden_provider
        captures = []
        async def run():
            request = {"action": "start", "job_id": "offline-frozen-preflight",
                       "limits": {"max_phases": 3, "max_calls": 12, "max_input_bytes": 1179648},
                       "pins": start_pins(before, old)}
            async for event in app.agent.stream(before.id, old["input"], planning_job=request):
                if event["type"] == "candidate_changed" and "第 1 次模型请求" in event["label"]:
                    record = app.unified.store.latest(before.id)
                    captures.append(record)
                    app.planning_jobs.cancel(before.id, request["job_id"])
        asyncio.run(run())
        assert len(captures) == 1
        new = captures[0]
        assert new.get("work_units") is None
        assert new["prior_work_units"] == old["work_units"]
        assert new["project"]["plan_contract"]["sources"][:-1] == old["project"]["plan_contract"]["sources"]
        assert new["project"]["plan_contract"]["sources"][-1]["text"] == old["input"]
        assert new["generation_progress"]["checkpoint_count"] == old["generation_progress"]["checkpoint_count"]
        assert all(app.unified.store.get(record["id"]) == record for record in original)
        names = [tool["function"]["name"] for tool in new["model_inputs"][0]["tools"]]
        assert set(names) == {"schedule_plan_changes", "ask_user"}
        assert app.db.get(before.id) == before
        held = schedule_findings(old, Project.model_validate(old["project"]))
        assert held
        assert boundary_kind(old, {"id": old["turn_id"], "number": 1, "kind": "generation",
                                   "starting_checkpoints": old["generation_progress"]["checkpoint_count"]}) is None
        return {"frozen_files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
                "candidate_id": old["id"], "base_revision": before.revision,
                "prior_candidates_preserved_exactly": len(original), "provider_dispatches": 0,
                "saved_holds_preserved": [finding["code"] for finding in held],
                "old_boundary_auto_continuation": False,
                "new_job_source_id": new["planning_job"]["source_id"],
                "previous_sources_unchanged": True, "feedback_exact": True,
                "first_request_tools": names,
                "retained_checkpoints": old["generation_progress"]["checkpoint_count"], "new_checkpoints": 0,
                "canonical_unchanged": True, "job_status": app.planning_jobs.store.get("offline-frozen-preflight")["status"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = preflight(args.frozen_dir)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
