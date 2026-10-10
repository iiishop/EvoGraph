"""Offline production-request measurements for bounded complete-change.

Uses a fresh temporary database restored from a frozen exported snapshot. Every
provider is a local scripted stub, credentials/adapters are blocked, and every
network connection is denied. These are protocol/volume proofs, not model-quality
evidence. No production profile, settings, repository or remote is modified.
"""
import argparse
import asyncio
import hashlib
import importlib.abc
import json
import socket
import sys
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODE = "bounded-complete-change/v1"
LIMITS = {"max_phases": 1, "max_calls": 2, "max_input_bytes": 393216}
R12_ID = "a9e8875730394375"
SYNTHETIC_INPUT = (
    "SYNTHETIC OFFLINE: make checker M1 consume check0 from prerequisite M0. "
    "Keep both existing acceptance statements and mechanisms; add M1 -> M0 and "
    "check1 -> check0 together in one complete structural delta."
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class OfflineGuard:
    """Fail closed before imports; only new temporary database paths are allowed."""

    def __init__(self, *, allow_loopback=False):
        self.allow_loopback = allow_loopback
        self.profiles = set()
        self.counts = {"network_denied": 0, "credential_accesses": 0,
                       "forbidden_imports": 0, "outside_profile_databases": 0}

    def register(self, profile):
        self.profiles.add(Path(profile).resolve())

    def install(self):
        guard = self

        class ForbiddenModules(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if (fullname.split(".")[0] in {"keyring", "keyrings"}
                        or (fullname.startswith("evograph.providers.")
                            and fullname != "evograph.providers.base")):
                    guard.counts["forbidden_imports"] += 1
                    raise RuntimeError("Offline fixture forbids credentials and real provider adapters")
                return None

        def check_address(address):
            if (guard.allow_loopback and isinstance(address, tuple)
                    and address[0] in {"127.0.0.1", "::1", "localhost"}):
                return
            guard.counts["network_denied"] += 1
            raise RuntimeError("Offline fixture refuses non-loopback/network connections")

        real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
        real_create_connection = socket.create_connection
        real_getaddrinfo = socket.getaddrinfo

        def connect(sock, address):
            check_address(address)
            return real_connect(sock, address)

        def connect_ex(sock, address):
            check_address(address)
            return real_connect_ex(sock, address)

        def create_connection(address, *args, **kwargs):
            check_address(address)
            return real_create_connection(address, *args, **kwargs)

        def getaddrinfo(host, port, *args, **kwargs):
            check_address((host, port))
            return real_getaddrinfo(host, port, *args, **kwargs)

        def audit(event, args):
            if event == "socket.connect":
                check_address(args[1])
            if event == "socket.sendto":
                check_address(args[-1])
            if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
                check_address((args[0], 0))
            if event == "sqlite3.connect":
                path = Path(str(args[0])).resolve()
                if not any(path.is_relative_to(profile) for profile in guard.profiles):
                    guard.counts["outside_profile_databases"] += 1
                    raise RuntimeError("Offline fixture refuses every database outside its temporary profile")

        socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
        socket.create_connection, socket.getaddrinfo = create_connection, getaddrinfo
        sys.meta_path.insert(0, ForbiddenModules())
        sys.addaudithook(audit)


class NoSecrets:
    def __init__(self, guard):
        self.guard = guard

    def get(self, name):
        self.guard.counts["credential_accesses"] += 1
        raise RuntimeError("Offline fixture forbids credential reads")

    def set(self, name, secret):
        self.guard.counts["credential_accesses"] += 1
        raise RuntimeError("Offline fixture forbids credential writes")


async def tool_chunks(name, arguments):
    yield {"type": "tool_delta", "index": 0, "id": "offline-fake", "name": name,
           "arguments": json.dumps(arguments, ensure_ascii=False)}
    yield {"type": "usage", "tokens": 7}


def request(app, project, job_id):
    from evograph.infrastructure.plan_jobs import start_pins
    return {"action": "start", "job_id": job_id, "mode": MODE,
            "limits": deepcopy(LIMITS),
            "pins": start_pins(project, app.unified.store.latest(project.id))}


def collect(app, project, req, content):
    async def run():
        return [event async for event in app.agent.stream(
            project.id, content, planning_job=req)]
    return asyncio.run(run())


def restore_snapshot(app, snapshot):
    from evograph.domain.models import Project
    manifest = json.loads((snapshot / "manifest.json").read_text())
    for name, expected in manifest.items():
        assert sha((snapshot / name).read_bytes()) == expected, name
    original = json.loads((snapshot / "candidates.json").read_text())
    canonical = Project.model_validate_json((snapshot / "project.json").read_text())
    assert len(original) == 12 and original[-1]["id"] == R12_ID
    assert original[-1]["revision"] == 12 and canonical.revision == 2
    app.db.create(canonical)
    with app.db.connect() as c:
        for record in original:
            c.execute("INSERT INTO plan_candidates VALUES(?,?,?,?)", (
                record["id"], canonical.id, json.dumps(record, ensure_ascii=False), record["created_at"]))
        for event in json.loads((snapshot / "events.json").read_text()):
            c.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                event["id"], canonical.id, event["kind"], event["detail"], event["created_at"]))
        for message in json.loads((snapshot / "messages.json").read_text()):
            c.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (
                message["id"], canonical.id, message["role"], message["content"], message["created_at"]))
        for job in json.loads((snapshot / "jobs.json").read_text()):
            c.execute("INSERT INTO planning_jobs VALUES(?,?,?)", (
                job["id"], canonical.id, json.dumps(job, ensure_ascii=False)))
        assert c.execute("SELECT count(*) FROM settings").fetchone()[0] == 0
    return canonical, original, manifest


def envelope(messages, schemas, options):
    value = {"messages": deepcopy(messages), "tools": deepcopy(schemas)}
    if "request_controls" in options:
        value["request_controls"] = deepcopy(options["request_controls"])
    assert set(options) <= {"request_controls"}, options
    return value


def describe(value):
    return {"input_bytes": len(compact(value)), "message_bytes": len(compact(value["messages"])),
            "schema_bytes": len(compact(value["tools"])),
            "compact_sha256": sha(compact(value)),
            "runtime_request_sha256": sha(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()),
            "tools": [row["function"]["name"] for row in value["tools"]]}


def export_capture(out, stem, capture):
    value = capture["request"]
    (out / (stem + ".compact.json")).write_bytes(compact(value))
    write_json(out / (stem + ".json"), capture)
    return {**describe(value), "request_file": stem + ".compact.json",
            "capture_file": stem + ".json"}


def context_completeness(app, record, captured):
    """Compare the actual dispatch context against exact pinned application data."""
    from evograph.application.plan_complete_change import (
        COMPLETE_CHANGE_PROMPT,
        complete_change_context,
    )
    from evograph.application.plan_continuation_context import prior_pending_intent_context
    from evograph.application.plan_units import _objects
    from evograph.application.retained_acceptance import (
        capture_entry,
        decode_router_retained,
        generation_context,
        resolutions,
    )
    from evograph.domain.models import Project
    from evograph.domain.plan_harness import content_hash
    system = captured["messages"][0]["content"]
    prefix, encoded = system.split("\nCurrent state (data):\n", 1)
    assert prefix == COMPLETE_CHANGE_PROMPT
    context = json.loads(encoded)
    project = Project.model_validate(record["project"])
    before = app.db.get(project.id)
    old = app.unified.store.get(record["resumes_candidate_id"])
    db = SimpleNamespace(project=project, record=record, source_id=record["planning_job"]["source_id"])
    assert context == complete_change_context(db)
    assert not record.get("work_units") and not record.get("unit_request")
    assert not {"schedule", "manifest_field_catalog", "current_unit"} & context.keys()
    objects = _objects(project)
    contracts = [value for key, value in objects.items() if key.startswith("contract:")]
    directory_fields = {"key", "owner", "revision_id", "statement", "acceptance_scope",
                        "requirement_ids", "component_ids", "requires_behavior_keys", "provides", "steps"}
    assert context["acceptance_directory"] == [
        {key: value for key, value in row.items() if key in directory_fields} for row in contracts]
    mechanisms = [{key: row[key] for key in ("key", "revision_id", "mechanism")} for row in contracts]
    assert context["completed_contract_mechanisms"] == mechanisms
    assert len(contracts) == len(mechanisms) == 8
    slice_fields = {"id", "title", "intent", "scope", "dependencies", "dependency_reasons",
                    "attachment_ids", "resources", "change_types"}
    assert context["slices"] == [row.model_dump(include=slice_fields) for row in project.milestones]
    assert len(context["slices"]) == 7
    source_fields = {"id", "title", "intent", "scope", "dependencies", "dependency_reasons",
                     "source_behaviors", "source_baseline_id"}
    assert context["source_slices"] == [row.model_dump(include=source_fields) for row in project.source_milestones]
    components = [{key: value for key, value in row.items()
                   if key in {"id", "label", "description", "role", "source_refs"}}
                  for identity, row in objects.items() if identity.startswith("component:")]
    assert context["components"] == components and all("source_refs" in row for row in components)
    assert context["relations"] == [{"id": identity.split(":", 1)[1], **row}
                                    for identity, row in objects.items() if identity.startswith("relation:")]
    architecture = objects["architecture:architecture"]
    metadata = {key: value for key, value in architecture.items() if key not in {"number", "created_at", "diagram"}}
    metadata["diagram"] = {key: value for key, value in architecture["diagram"].items() if key not in {"nodes", "edges"}}
    assert context["architecture_context"]["metadata"] == metadata
    sources, texts = [], {}
    for row in context["sources"]:
        value = deepcopy(row)
        if "same_text_as_source_id" in value:
            value["text"] = texts[value.pop("same_text_as_source_id")]
        texts[value["id"]] = value["text"]
        sources.append(value)
    assert sources == [row.model_dump(exclude={"activity"}) for row in project.plan_contract.sources]
    assert context["requirements"] == [row.model_dump() for row in project.plan_contract.requirements]
    assert context["process_constraints"] == [row.model_dump() for row in project.plan_contract.process_constraints]
    retained = decode_router_retained(context)
    expected_retained = {**generation_context(record, project), "entry_pins": record["retained_acceptance"]["pins"]}
    assert retained == expected_retained
    fresh = capture_entry(before, old, source_id=db.source_id, audit_start=0)
    assert record["retained_acceptance"] == fresh
    old_resolutions = resolutions(old["retained_acceptance"], old, Project.model_validate(old["project"]))
    current_resolutions = resolutions(fresh, record, project)
    canonical_claims = [row for row in fresh["claims"] if row["origin"] == "canonical_accepted"]
    staged_claims = [row for row in fresh["claims"] if row["origin"] == "staged_draft"]
    for row in canonical_claims:
        actual = {key: value for key, value in current_resolutions[row["id"]].items() if key != "inherited_proposal"}
        prior = {key: value for key, value in old_resolutions[row["id"]].items() if key != "inherited_proposal"}
        assert actual == prior
    assert len(canonical_claims) == 4 and len(staged_claims) == 8
    assert record["prior_work_units"] == old["work_units"]
    pending = prior_pending_intent_context(record, project.id)
    assert context["prior_work_units"] == pending and pending
    historical = old["work_units"]["manifest"]
    assert len(historical) == 13
    assert sum(row["kind"] == "slice" for row in historical) == 3
    assert sum(row["kind"] == "contract" for row in historical) == 5
    for field in ("reference_context", "inherited_findings", "inherited_semantic_findings",
                  "allowed_requirement_source_ids", "typed_obligation_keys", "consumption_obligation_keys"):
        assert context[field] == record.get(field, [])
    assert context["findings"] == record["report"].get("findings", [])
    return {"actual_dispatch_context_exact": True, "context_sha256": content_hash(context),
            "current_contract_statements": len(contracts), "current_contract_mechanisms": len(mechanisms),
            "statement_and_mechanism_projection_sha256": content_hash({
                "statements": context["acceptance_directory"], "mechanisms": mechanisms}),
            "current_slices_with_exact_dependency_reasons": len(context["slices"]),
            "slices_sha256": content_hash(context["slices"]),
            "components_with_exact_source_refs": len(components), "components_sha256": content_hash(components),
            "exact_architecture_metadata_and_relations": True,
            "requirements": len(context["requirements"]), "sources_losslessly_resolved": len(sources),
            "sources_sha256": content_hash(sources), "requirements_sha256": content_hash(context["requirements"]),
            "retained_canonical_originals": len(canonical_claims), "retained_fresh_R12_originals": len(staged_claims),
            "retained_aliases_losslessly_resolved": True, "retained_resolved_sha256": content_hash(retained),
            "canonical_disposition_continuity": True, "fresh_entry_equals_runtime": True,
            "exact_pending_proposal_projection": True, "pending_projection_sha256": content_hash(pending),
            "pending_historical_units": len(pending["pending_ids"]),
            "historical_manifest_identities": len(historical), "historical_manifest_slices": 3,
            "historical_manifest_contracts": 5, "historical_manifest_is_not_current_manifest": True,
            "current_manifest_fabricated": False, "source_slice_count": len(context["source_slices"])}


def capture_provider(app, *, synthetic=False, delay=0):
    captures = []

    async def stream(messages, schemas, **options):
        if delay:
            await asyncio.sleep(delay)
        record = app.unified.store.latest(app.db.list_projects()[0].id)
        value = envelope(messages, schemas, options)
        captures.append({"request": value, "options": deepcopy(options),
                         "candidate_id": record["id"], "candidate_revision": record["revision"]})
        if not synthetic:
            captures[-1]["context_completeness"] = context_completeness(app, record, value)
        names = {row["function"]["name"] for row in schemas}
        if names == {"submit_plan_review"} and synthetic:
            packet = json.loads(messages[-1]["content"])
            raw = {"candidate_hash": packet["candidate_hash"],
                   "review_scope_hash": packet["review_scope_hash"],
                   "summary": "SYNTHETIC scripted support; NOT model-quality evidence",
                   "issues": [], "observations": []}
            parameters = schemas[0]["function"]["parameters"]["properties"]
            if "coverage" in parameters:
                raw["coverage"] = {subject: "supported" for subject in packet["required_subjects"]}
            else:
                raw["statuses"] = {"supported": packet["required_subjects"],
                                   "contradicted": [], "unknown": []}
            name = "submit_plan_review"
        else:
            assert names == {"submit_plan_delta", "ask_user"}, names
            if synthetic:
                name, raw = "submit_plan_delta", {
                    "summary": "SYNTHETIC: align declared checker consumption with delivery prerequisite",
                    "contracts": [{"key": "check1", "requires_behavior_keys": ["check0"]}],
                    "slices": [{"id": "M1", "dependencies": ["M0"],
                                "dependency_reasons": {"M0": "Consume check0 output"}}]}
            else:
                name, raw = "ask_user", {
                    "prompt": "OFFLINE FIXTURE: stop this volume-only probe here?", "category": "decision",
                    "options": ["Stop fixture", "Inspect captured request"],
                    "context": "Scripted measurement stop; no proposal, real provider or quality claim."}
        async for event in tool_chunks(name, raw):
            yield event

    app.settings.stream = stream
    return captures


def synthetic_project(app):
    from evograph.domain.models import Project
    text = "Report local broken Markdown links without modifying source files."
    project = Project.model_validate({
        "id": "synthetic-complete-change", "name": "SYNTHETIC · Complete change · Fake provider only",
        "description": "Local fake-provider protocol fixture; not evidence of model planning quality.",
        "unified_planning": True,
        "targets": [{"number": 1, "statement": text, "required_behavior_ids": ["B0", "B1"]}],
        "milestones": [{"id": f"M{i}", "title": f"Checker {i}", "intent": text,
                        "scope": [f"checker{i}.py"], "architecture_components": ["core"],
                        "architecture_revision": 1, "behavior_revision_ids": [f"B{i}"]}
                       for i in range(2)],
        "behaviors": [{"id": f"B{i}", "version": 1, "behavior_key": f"check{i}",
                       "owner": f"M{i}", "statement": f"Report local broken links in section {i}"}
                      for i in range(2)],
        "plan_contract": {"sources": [{"id": "original", "text": text}],
                          "requirements": [{"id": "r", "source_id": "original", "quote": text}],
                          "bindings": [{"behavior_key": f"check{i}", "behavior_revision_id": f"B{i}",
                                        "requirement_ids": ["r"], "component_ids": ["core"],
                                        "mechanism": "Read-only local Markdown traversal and reporting."}
                                       for i in range(2)]},
        "architectures": [{"number": 1, "summary": "Local read-only checker",
                           "technologies": [{"area": "runtime", "choice": "Python",
                                             "rationale": "Local execution"}],
                           "diagram": {"id": "design", "title": "Checker",
                                       "nodes": [{"id": "core", "label": "Core",
                                                  "description": "Parse and report local links"}],
                                       "milestone_ids": ["M0", "M1"]}}]})
    app.db.create(project)
    return project


def proxy_review(before, record):
    """Exact production challenge request for saved content; no generation/review result."""
    from evograph.application.plan_budget import BudgetedSettings
    from evograph.application.unified_planning import challenge
    from evograph.domain.models import Project
    captures = []

    class Captured(Exception):
        pass

    async def stop_after_capture(messages, schemas, **options):
        captures.append({"request": envelope(messages, schemas, options), "options": deepcopy(options),
                         "candidate_id": record["id"], "candidate_revision": record["revision"],
                         "proxy_only": True, "generated_for_this_request": False})
        raise Captured()
        yield  # pragma: no cover

    metrics = {"provider_calls": 0, "tokens": 0, "input_bytes": 0,
               "budget": {"max_request_bytes": 2000000, "max_review_request_bytes": 2000000,
                          "max_total_input_bytes": 2000000}}
    settings = BudgetedSettings(SimpleNamespace(stream=stop_after_capture, secrets=None),
                                metrics, purpose="semantic_review")
    try:
        asyncio.run(challenge(settings, before, Project.model_validate(record["project"]), deepcopy(record)))
    except Captured:
        pass
    assert len(captures) == 1
    return captures[0]


def boundary_probe(capture, cap, *, purpose):
    from evograph.application.plan_budget import BudgetedSettings, BudgetExceededError
    results = []
    for extra in (0, 1):
        value = deepcopy(capture["request"])
        missing = cap + extra - len(compact(value))
        assert missing >= 0, "Base measured request must fit before an additive boundary test"
        value["messages"][-1]["content"] += "x" * missing
        assert len(compact(value)) == cap + extra
        calls = []

        async def fake(messages, schemas, **options):
            calls.append(envelope(messages, schemas, options))
            yield {"type": "usage", "tokens": 1}

        metrics = {"provider_calls": 0, "tokens": 0, "input_bytes": 0,
                   "budget": {"max_calls": 2, "max_request_bytes": cap,
                              "max_review_request_bytes": cap, "max_total_input_bytes": 393216}}
        wrapped = BudgetedSettings(SimpleNamespace(stream=fake, secrets=None), metrics,
                                   purpose=purpose, request_controls=value.get("request_controls"))

        async def run():
            try:
                return [event async for event in wrapped.stream(value["messages"], value["tools"])]
            except BudgetExceededError:
                return []

        asyncio.run(run())
        assert len(calls) == (0 if extra else 1)
        if extra:
            assert metrics["admission_rejections"][-1]["reason"] == "request_input_limit"
            assert metrics["provider_calls"] == metrics["input_bytes"] == 0
        results.append({"purpose": purpose, "input_bytes": len(compact(value)), "cap": cap,
                        "fake_provider_dispatches": len(calls), "metrics": metrics,
                        "actual_base_capture_bytes": len(compact(capture["request"])),
                        "fixture_padding_bytes": missing})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out, snapshot = args.output.resolve(), args.snapshot.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "backend"))
    guard = OfflineGuard()
    guard.install()
    source_hashes = {str(p.relative_to(ROOT)): sha(p.read_bytes())
                     for p in sorted((ROOT / "backend/evograph").rglob("*.py"))}
    from evograph.application.api import Application
    from evograph.domain.plan_contracts import planning_payload
    from evograph.domain.plan_harness import content_hash

    with TemporaryDirectory(prefix="evograph-complete-change-measure-") as directory:
        profile = Path(directory)
        guard.register(profile)
        app = Application(profile / "frozen-copy", NoSecrets(guard))
        before, originals, snapshot_manifest = restore_snapshot(app, snapshot)
        old = originals[-1]
        captures = capture_provider(app)
        req = request(app, before, "offline-frozen-complete-change")
        events = collect(app, before, req, old["input"])
        write_json(out / "frozen-run-events.json", events)
        assert len(captures) == 1, (len(captures), events[-3:])
        record = app.unified.store.latest(before.id)
        job = app.planning_jobs.store.get(req["job_id"])
        assert job["status"] == "stopped" and job["stop_reason"] == "question", job
        assert all(app.unified.store.get(row["id"]) == row for row in originals)
        old_jobs = json.loads((snapshot / "jobs.json").read_text())
        assert all(app.planning_jobs.store.get(row["id"]) == row for row in old_jobs)
        old_messages = json.loads((snapshot / "messages.json").read_text())
        saved_messages = {row["id"]: row for row in app.db.messages(before.id)}
        assert all({key: saved_messages[row["id"]][key] for key in row} == row
                   for row in old_messages)
        with app.db.connect() as c:
            count = c.execute("SELECT count(*) FROM plan_candidates").fetchone()[0]
        assert count == 13
        pins = record["retained_acceptance"]["pins"]
        assert pins["staged_candidate_id"] == R12_ID
        assert pins["staged_record_hash"] == content_hash(old)
        assert pins["source_id"] == job["source_id"] != old["planning_job"]["source_id"]
        assert record["resumes_candidate_id"] == R12_ID
        assert record["project"]["plan_contract"]["sources"][:-1] == old["project"]["plan_contract"]["sources"]
        assert record["project"]["plan_contract"]["sources"][-1]["text"] == old["input"]
        assert planning_payload(app.db.get(before.id)) == planning_payload(before)
        actual = export_capture(out, "frozen-generation-actual", captures[0])
        write_json(out / "frozen-context-completeness.json", captures[0]["context_completeness"])
        receipt = record["metrics"]["calls"][0]
        for field in ("input_bytes", "message_bytes", "schema_bytes"):
            assert receipt[field] == actual[field], (field, receipt[field], actual[field])
        assert receipt["request_sha256"] == actual["runtime_request_sha256"]
        assert record["model_inputs"][0] == captures[0]["request"]
        write_json(out / "frozen-new-job.json", job)
        write_json(out / "frozen-new-candidate.json", record)
        write_json(out / "frozen-new-canonical.json", app.db.get(before.id).model_dump())
        proxy = proxy_review(before, old)
        proxy_desc = export_capture(out, "r12-review-PROXY", proxy)
        successor_proxy = proxy_review(before, record)
        successor_desc = export_capture(out, "r13-new-entry-review-PROXY", successor_proxy)
        boundaries = boundary_probe(captures[0], receipt["request_limit_bytes"], purpose="generation")
        review_cap = record["metrics"]["budget"].get("max_review_request_bytes", receipt["request_limit_bytes"])
        if successor_desc["input_bytes"] <= review_cap:
            boundaries += boundary_probe(successor_proxy, review_cap, purpose="semantic_review")
        write_json(out / "request-boundaries.json", boundaries)
        frozen_summary = {"snapshot": str(snapshot), "snapshot_manifest": snapshot_manifest,
                          "original_candidate_count": len(originals), "original_candidates_unchanged": True,
                          "original_jobs_unchanged": True, "original_messages_unchanged": True,
                          "candidate_count_after": count, "current_base_revision": before.revision,
                          "previous_candidate_id": R12_ID, "new_candidate_id": record["id"],
                          "new_candidate_revision": record["revision"], "new_source_id": job["source_id"],
                          "new_entry_pins": pins, "job_status": job["status"],
                          "stop_reason": job["stop_reason"], "limits": job["limits"],
                          "phase_budget": record["metrics"]["budget"], "fake_provider_dispatches": len(captures),
                          "new_compiler_checkpoints": record["generation_progress"]["checkpoint_count"]
                              - old["generation_progress"]["checkpoint_count"],
                          "canonical_planning_unchanged": True,
                          "canonical_question_state_added": bool(app.db.get(before.id).question),
                          "context_completeness": captures[0]["context_completeness"],
                          "actual_generation": actual, "current_r12_review_proxy": proxy_desc,
                          "new_r13_entry_review_proxy": successor_desc,
                          "proxy_warning": "No generation delta or semantic review ran for this real snapshot. "
                              "Review envelopes use saved content, not an unknown future generated candidate."}

        synthetic = Application(profile / "synthetic", NoSecrets(guard))
        project = synthetic_project(synthetic)
        synthetic_calls = capture_provider(synthetic, synthetic=True)
        synthetic_req = request(synthetic, project, "offline-synthetic-complete-change")
        synthetic_events = collect(synthetic, project, synthetic_req, SYNTHETIC_INPUT)
        write_json(out / "synthetic-events.json", synthetic_events)
        synthetic_record = synthetic.unified.store.latest(project.id)
        synthetic_job = synthetic.planning_jobs.store.get(synthetic_req["job_id"])
        write_json(out / "synthetic-candidate.json", synthetic_record)
        write_json(out / "synthetic-job.json", synthetic_job)
        assert synthetic_job["status"] == "applied", (synthetic_job, synthetic_events[-5:])
        assert len(synthetic_calls) == 2
        for captured, charged in zip(synthetic_calls, synthetic_record["metrics"]["calls"], strict=True):
            measured = describe(captured["request"])
            assert charged["request_sha256"] == measured["runtime_request_sha256"]
            assert charged["input_bytes"] == measured["input_bytes"]
            assert charged["message_bytes"] == measured["message_bytes"]
            assert charged["schema_bytes"] == measured["schema_bytes"]
        applied = synthetic.db.get(project.id)
        assert applied.milestone("M1").dependencies == ["M0"]
        assert next(b for b in applied.plan_contract.bindings if b.behavior_key == "check1").requires_behavior_keys == ["check0"]
        synthetic_summary = {"synthetic_only": True, "fake_review_is_quality_evidence": False,
                             "job_status": synthetic_job["status"], "limits": synthetic_job["limits"],
                             "fake_provider_dispatches": len(synthetic_calls),
                             "canonical_revision_before": project.revision,
                             "canonical_revision_after": applied.revision,
                             "structural_delta": {"M1.dependencies": ["M0"], "check1.requires_behavior_keys": ["check0"]},
                             "captures": [export_capture(out, "synthetic-request-" + str(i + 1), c)
                                          for i, c in enumerate(synthetic_calls)]}
        write_json(out / "synthetic-canonical.json", applied.model_dump())

    for name, expected in snapshot_manifest.items():
        assert sha((snapshot / name).read_bytes()) == expected, name
    assert source_hashes == {str(p.relative_to(ROOT)): sha(p.read_bytes())
                            for p in sorted((ROOT / "backend/evograph").rglob("*.py"))}, (
        "Backend changed while measuring; rerun after the isolated implementation is stable")
    summary = {"mode": MODE, "measurement_contract": "Exact compact UTF-8 application envelope of messages/tools/request_controls; not HTTP transport bytes or provider tokens",
               "safety": {**guard.counts, "real_provider_calls": 0, "settings_loaded": False,
                          "real_profile_loaded": False, "gui_started": False, "network_policy": "deny all"},
               "frozen_runtime": frozen_summary, "synthetic_runtime": synthetic_summary,
               "boundary_probe_count": len(boundaries),
               "source_unchanged_during_run": True, "source_hashes": source_hashes,
               "tool_hashes": {p.name: sha(p.read_bytes()) for p in (
                   ROOT / "tools/measure_complete_change.py", ROOT / "tools/preview_complete_change.py")}}
    write_json(out / "measurements.json", summary)
    (out / "REPORT.md").write_text(
        "# Offline bounded complete-change measurements\n\n"
        "Production application runtime, frozen R12/formal R2 copied to a new temporary database. "
        "All 12 original candidate records, old jobs/messages and frozen snapshot file hashes remain unchanged. "
        "The runtime creates source, retained-entry pins and candidate 13; the fake generation asks a scripted "
        "question and stops. No proposed repair or real semantic review was run on this project.\n\n"
        f"- Mode: `{MODE}`\n"
        "- Authorization envelope: 1 phase / 2 calls / 393,216 input bytes\n"
        f"- Actual generation: **{actual['input_bytes']:,} B**, including "
        f"{actual['message_bytes']:,} B messages and {actual['schema_bytes']:,} B schemas\n"
        f"- Margin below 163,840 B per-request cap: {163840 - actual['input_bytes']:,} B\n"
        f"- Generation compact SHA-256: `{actual['compact_sha256']}`\n"
        f"- Runtime audit hash: `{actual['runtime_request_sha256']}`\n"
        f"- Current R12 saved-content review proxy: {proxy_desc['input_bytes']:,} B\n"
        f"- New-entry successor saved-content review proxy: {successor_desc['input_bytes']:,} B\n"
        f"- Generation plus successor review sizing proxy: {actual['input_bytes'] + successor_desc['input_bytes']:,} B\n\n"
        "Review proxies are production challenge envelopes for saved content, not measured requests for an "
        "unknown future generated repair. They cannot establish that a future repair will fit or pass review. "
        "Bytes mean the compact UTF-8 application messages/tools/request_controls envelope, not HTTP transport "
        "bytes, model tokens or pricing. Runtime receipt hashes use the existing sorted-key, normal-spacing "
        "serialization; compact-file hashes are separately identified.\n\n"
        "## Verified protocol checks\n\n"
        "- Actual dispatch context equals the production projection: all 8 active statements and mechanisms, "
        "7 slices with exact dependency reasons, 7 components with exact source refs, 15 requirements and "
        "11 losslessly decoded sources. Existing alias resolver recovers all 4 canonical and 8 fresh R12 "
        "retained originals; canonical dispositions continue exactly. Exact pending proposal projection "
        "is preserved. The historical 13-identity/3-slice/5-contract scope is not a fabricated current manifest\n"
        "- Frozen runtime: exactly one fake generation dispatch; question terminal, zero new compiler "
        "checkpoints, canonical planning unchanged (ordinary question state was added)\n"
        "- Separate, wholly synthetic project: one structural delta adds M1 → M0 and check1 → check0 "
        "together, then one scripted passing review; applied in exactly two fake dispatches\n"
        "- Both synthetic request captures exactly match runtime charged bytes and hashes\n"
        "- Actual captured generation/review messages and schemas padded to 163,840 and 163,841 B: "
        "the production BudgetedSettings admits exactly one fake dispatch at cap and zero at cap+1, "
        "with an admission-rejection audit entry; these are budget-wrapper checks, not a second job\n"
        "- Zero real provider dispatches, credential accesses, external network connections or GUI starts\n\n"
        "## Reproduce\n\n"
        "From the isolated worktree, run the existing project Python environment with:\n\n"
        "```sh\npython tools/measure_complete_change.py \\\n"
        "  --snapshot ../evograph-recovery/qa55-bounded-experiment/22-retained-acceptance-job-stopped-result \\\n"
        "  --output ../evograph-recovery/qa55-bounded-experiment/complete-change-offline\n"
        "python tools/preview_complete_change.py --check\n```\n\n"
        "The optional native launcher opens only a fresh, labelled synthetic profile and requires a verified "
        "frontend build. It has not been opened for these measurements; its --check evidence is headless. "
        "NoSecrets, adapter-import guard, outside-profile SQLite guard and non-loopback network guard "
        "remain enabled. Fake reviews prove wiring only, never real-model planning quality.\n"
    )
    write_json(out / "artifact-manifest.json", {str(p.relative_to(out)): sha(p.read_bytes())
               for p in sorted(out.rglob("*")) if p.is_file() and p.name != "artifact-manifest.json"})
    print(json.dumps({"output": str(out), "generation": actual, "review_proxy": successor_desc,
                      "synthetic_status": synthetic_summary["job_status"],
                      "boundaries": len(boundaries), "safety": summary["safety"]}, indent=2))


if __name__ == "__main__":
    main()
