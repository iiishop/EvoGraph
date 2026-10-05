"""Read-only frozen accepted-delta replay, entirely in private memory.

Usage: PYTHONPATH=backend python tools/replay_bounded_cold_start.py FROZEN_DIRECTORY REPORT.json
No model/settings/credentials/database/application startup; never a live joint-response claim.
"""
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from evograph.agent_tools.base import ToolContext
from evograph.application.plan_batch_policy import COLD_START_EXPERIMENT, experiment_policy
from evograph.application.plan_budget import compact_schema, encoded_size
from evograph.application.plan_harness import run_deterministic, seal_snapshot
from evograph.application.plan_ir import DELTA_TOOL, PlanDelta, submit_plan_delta
from evograph.application.plan_stage import StagedDatabase
from evograph.application.plan_units import (
    SchedulePlanChanges,
    _hash,
    _operation_count,
    _schedule,
    _unit_changes,
    all_units_complete,
    prepare_unit_request,
    schedule_findings,
    schedule_plan_changes,
)
from evograph.application.unified_planning import generator_prompt
from evograph.domain.models import Project
from evograph.domain.plan_contracts import candidate_hash


class MemoryStore:
    def save(self, record):
        value = deepcopy(record)
        value["candidate_hash"] = candidate_hash(Project.model_validate(value["project"]))
        return value


class NoCanonicalIO:
    def __init__(self, frozen):
        self.frozen = frozen

    def get(self, project_id):
        assert self.frozen.id == project_id
        return self.frozen.model_copy(deep=True)

    def __getattr__(self, name):
        raise AssertionError("Offline replay may not change canonical data: " + name)


def replay(root):
    source_names = ("candidates.json", "candidate-project.json", "project.json")
    frozen = {name: (root / name).read_bytes() for name in source_names}
    records = json.loads(frozen["candidates.json"])
    latest = records[-1]
    raw = {}
    for record in records:
        for audit in record.get("compilations", []):
            for key, value in audit["ir"].items():
                if isinstance(value, list):
                    raw.setdefault(key, []).extend(deepcopy(value))
                elif key == "summary" or key not in raw:
                    raw[key] = deepcopy(value)
                else:
                    assert raw[key] == value
    delta = PlanDelta.model_validate(raw)
    base = Project.model_validate(records[0]["project"])
    # Keep each real historical source/quote/ID; this replay is not a claim that
    # later user input existed at the first original user turn.
    base.plan_contract.sources = Project.model_validate(latest["project"]).plan_contract.sources
    for requirement in raw["requirements"]:
        source = next(s for s in base.plan_contract.sources if s.id == requirement["source_id"])
        assert requirement["quote"] in source.text
    record = {"id": latest["id"], "turn_id": latest["id"], "project_id": base.id,
              "project": base.model_dump(mode="json"), "base_revision": base.revision,
              "status": "offline_reconstruction", "report": {}, "input": latest["input"],
              "reference_context": deepcopy(latest["reference_context"]),
              "allowed_requirement_source_ids": deepcopy(latest["allowed_requirement_source_ids"]),
              "planning_experiment": {"project_id": base.id, **experiment_policy(COLD_START_EXPERIMENT)}}
    canonical = Project.model_validate(json.loads(frozen["project.json"]))
    db = StagedDatabase(NoCanonicalIO(canonical), MemoryStore(), record, latest["id"])
    db.segmented_planning = True
    ctx = ToolContext(base.id, SimpleNamespace(db=db))
    manifest = deepcopy(latest["work_units"]["manifest"])
    segmented = _schedule(base, _unit_changes(manifest))
    schedule_plan_changes(ctx, SchedulePlanChanges.model_validate({"changes": [
        {k: r[k] for k in ("kind", "id", "fields", "uses", "intent")} for r in manifest]}))
    assert db.record["work_units"]["manifest"] == manifest
    context = prepare_unit_request(db)
    request = {"messages": [{"role": "system", "content": generator_prompt(db.record)
               + "\nCurrent saved candidate and assigned work unit (data):\n"
               + json.dumps({**context, "allowed_requirement_source_ids": sorted(db.requirement_source_ids)}, ensure_ascii=False)},
               {"role": "user", "content": latest["input"]}],
               "tools": [compact_schema(DELTA_TOOL.schema())]}
    assert not schedule_findings(db.record, base)
    result = submit_plan_delta(ctx, delta)
    assert result["candidate_state"] == "staged" and all_units_complete(db.record)
    assert len(db.record["work_units"]["checkpoints"]) == 1
    run = run_deterministic(seal_snapshot(base, db.project, db.record))
    assert not [e for e in run.executions if e.result and e.result.verdict == "block"]
    expected = Project.model_validate(json.loads(frozen["candidate-project.json"]))
    assert db.project.plan_contract.requirements == expected.plan_contract.requirements
    assert db.project.plan_contract.sources == base.plan_contract.sources
    assert db.project.architectures[-1].summary == expected.architectures[-1].summary
    assert db.project.architectures[-1].technologies == expected.architectures[-1].technologies
    for name, value in frozen.items():
        assert (root / name).read_bytes() == value
    return {"scope": "Offline reconstructed accepted deltas, not actual joint model generation or calls saved",
            "frozen_files": {name: {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
                             for name, value in frozen.items()},
            "historical_sources": [s.id for s in base.plan_contract.sources],
            "historical_source_limit": "Later sources retained independently for reconstruction; not asserted present in original first turn",
            "manifest_hash": _hash(manifest), "policy": db.record["work_units"]["batch_admission"]["policy"],
            "canonical_operations": _operation_count(_unit_changes(manifest)),
            "field_atoms": len(_unit_changes(manifest)), "reconstructed_delta_bytes": encoded_size(raw),
            "default_segmented_units": len(segmented), "batch_units": 1,
            "actual_context_bytes": encoded_size(context), "projected_generator_request_bytes": encoded_size(request),
            "atomic_checkpoints": len(db.record["work_units"]["checkpoints"]),
            "deterministic_plugins": [{"id": e.plugin_id, "status": e.status,
                "verdict": e.result.verdict if e.result else None,
                "findings": [f.model_dump(mode="json") for f in e.result.findings] if e.result else []}
                for e in run.executions],
            "semantic_review": "not run; no old certificate reused", "provider_calls": 0,
            "publication": "not attempted", "frozen_inputs_unchanged": True}


if __name__ == "__main__":
    report = replay(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
