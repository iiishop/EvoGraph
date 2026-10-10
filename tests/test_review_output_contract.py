"""Packet-specific output admission; fixtures never claim model-quality validation."""
import asyncio
import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from evograph.application.plan_budget import compact_schema
from evograph.application.plan_harness import _semantic_result, run_harness, seal_snapshot
from evograph.application.plan_recheck import new_review_attempt, review_projection
from evograph.application.plan_review import (
    SCOPED_BATCH_VERSION,
    BatchSemanticReview,
    ScopedBatchSemanticReview,
    _checked_refs,
    _expanded_evidence_catalog,
    _validate_materiality,
    batch_review_certificate,
    batch_review_packet,
    normalize_batch_review,
    parse_batch_certificate,
    parse_batch_review,
    review_output_schema,
    validate_batch_certificate,
)
from evograph.application.unified_planning import REVIEWER, reviewer_prompt
from evograph.domain.models import Project
from evograph.domain.plan_contracts import review_subjects
from evograph.domain.plan_harness import canonical_json
from test_review_materiality import batch_for, context, material_issue, observation

# Re-export the explicit fixture for pytest, without changing its v2 packet.
__all__ = ["context"]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def scoped(context):
    project, record, _, _ = context
    record = {**record, "review_protocol": SCOPED_BATCH_VERSION}
    return project, record, seal_snapshot(project, project, record), batch_review_packet(
        project, project, record)


def test_schema_exact_subjects_catalog_and_distinct_reference_rules(scoped):
    _, _, _, packet = scoped
    schema = compact_schema(review_output_schema(packet))
    coverage = schema["properties"]["coverage"]
    assert "statuses" not in schema["properties"] and "coverage" in schema["required"]
    assert coverage["required"] == packet["required_subjects"]
    assert list(coverage["properties"]) == packet["required_subjects"]
    assert coverage["additionalProperties"] is False
    assert set(canonical_json(x) for x in coverage["properties"].values()) == {
        canonical_json({"$ref": "#/$defs/ReviewVerdict"})}
    assert schema["$defs"]["ReviewVerdict"] == {
        "type": "string", "enum": ["supported", "contradicted", "unknown"]}
    assert schema["$defs"]["ReviewSubject"]["enum"] == packet["required_subjects"]
    for name in ("BatchIssue", "BatchObservation"):
        assert schema["$defs"][name]["properties"]["subjects"]["items"] == {
            "$ref": "#/$defs/ReviewSubject"}
    issue_refs = schema["$defs"]["BatchIssue"]["properties"]["evidence_refs"]["items"]
    assert issue_refs["enum"] == _expanded_evidence_catalog(packet)
    legacy = compact_schema(BatchSemanticReview.model_json_schema())
    assert schema["$defs"]["MaterialityEvidence"] == legacy["$defs"]["MaterialityEvidence"]
    assert schema["$defs"]["BatchObservation"]["properties"]["evidence_refs"] == (
        legacy["$defs"]["BatchObservation"]["properties"]["evidence_refs"])
    for field in ("candidate_hash", "review_scope_hash"):
        assert schema["properties"][field]["enum"] == [packet[field]]
    # Compaction must retain arbitrary subject properties, including "title".
    renamed = {**packet, "required_subjects": ["title", "a/b~c"]}
    assert set(compact_schema(review_output_schema(renamed))["properties"]["coverage"]["properties"]) == {
        "title", "a/b~c"}


def test_schema_is_pure_and_legacy_schema_prompt_stay_exact(context, scoped):
    for packet in (context[3], scoped[3]):
        old = deepcopy(packet)
        assert review_output_schema(packet) == review_output_schema(packet)
        assert packet == old
    assert review_output_schema(context[3]) == BatchSemanticReview.model_json_schema()
    assert reviewer_prompt(context[3]) == REVIEWER
    assert "Do not omit any subject" in reviewer_prompt(scoped[3])


def test_v3_keeps_exact_raw_and_replays_shared_harness(scoped):
    project, record, snapshot, packet = scoped
    batch = batch_for(packet, issues=[material_issue()], observations=[observation()])
    raw = json.dumps(batch.model_dump(), ensure_ascii=False, indent=3)
    assert isinstance(parse_batch_review(raw, packet), ScopedBatchSemanticReview)
    review = normalize_batch_review(project, packet, batch)
    certificate = batch_review_certificate(raw, batch)
    assert certificate["raw_arguments"] == raw and certificate["schema_version"] == SCOPED_BATCH_VERSION
    assert parse_batch_certificate(packet, certificate) == batch
    result, _, _ = _semantic_result(snapshot, review, certificate)
    assert result.verdict == "unknown"
    record.update(review_inputs=[packet], batch_reviews=[certificate], reviews=[review.model_dump()],
                  report={"semantic": review.model_dump(), "semantic_batch": batch.model_dump()})
    assert validate_batch_certificate(project, project, record) == review


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong_verdict", "wrong_type",
                                       "legacy_statuses", "hash", "scope", "issue_mismatch",
                                       "issue_missing", "issue_extra", "duplicate_issue_subject"])
def test_v3_never_infers_missing_coverage_or_accepts_mismatches(scoped, mutation):
    project, _, _, packet = scoped
    value = batch_for(packet, issues=[material_issue()]).model_dump()
    if mutation == "missing":
        del value["coverage"]["slice_activation:M1"]  # Still present in the issue.
    elif mutation == "extra":
        value["coverage"]["slice_activation:invented"] = "supported"
    elif mutation == "wrong_verdict":
        value["coverage"]["slice_activation:M1"] = "pass"
    elif mutation == "wrong_type":
        value["coverage"]["slice_activation:M1"] = ["unknown"]
    elif mutation == "legacy_statuses":
        value["statuses"] = {"supported": [], "unknown": [], "contradicted": []}
        del value["coverage"]
    elif mutation in {"hash", "scope"}:
        value["candidate_hash" if mutation == "hash" else "review_scope_hash"] = "stale"
    elif mutation == "issue_mismatch":
        value["coverage"]["slice_activation:M1"] = "supported"
    elif mutation == "issue_missing":
        value["issues"] = []
    elif mutation == "issue_extra":
        value["issues"][0]["subjects"].append("invented")
    elif mutation == "duplicate_issue_subject":
        value["issues"][0]["subjects"].append("slice_activation:M1")
    with pytest.raises(ValueError):
        normalize_batch_review(project, packet, parse_batch_review(json.dumps(value), packet))


@pytest.mark.parametrize("field,pointer", [
    ("evidence_refs", "/candidate/behaviors/0/statement"),
    ("evidence_refs", "/candidate/behaviors/999"),
    ("boundary_refs", "/candidate/behaviors/999/statement"),
    ("obligation_ref", "/candidate/plan_contract/bindings/0/mechanism"),
    ("provider_ref", "M2"),
])
def test_v3_exact_evidence_and_materiality_are_still_authoritative(scoped, field, pointer):
    project, _, _, packet = scoped
    issue = material_issue()
    if field == "evidence_refs":
        issue[field] = [pointer]
    elif field == "boundary_refs":
        issue["materiality"][field] = [pointer]
    else:
        issue["materiality"][field] = pointer
    with pytest.raises(ValueError):
        normalize_batch_review(project, packet, batch_for(packet, issues=[issue]))


def test_valid_descendants_remain_valid_only_in_their_own_fields(scoped):
    project, _, _, packet = scoped
    batch = batch_for(packet, issues=[material_issue()], observations=[observation()])
    assert normalize_batch_review(project, packet, batch)
    assert batch.issues[0].materiality.boundary_refs[0].endswith("/mechanism")
    assert batch.observations[0].evidence_refs[0].endswith("/M1")
    refs = review_output_schema(packet)["$defs"]["BatchIssue"]["properties"]["evidence_refs"]["items"]["enum"]
    assert batch.issues[0].materiality.boundary_refs[0] not in refs


@pytest.mark.parametrize("place", ["root", "coverage", "materiality"])
def test_duplicate_json_keys_rejected_before_any_projection(context, place):
    protocol = SCOPED_BATCH_VERSION
    project, record, _, _ = context
    packet = batch_review_packet(project, project, {**record, "review_protocol": protocol})
    raw = batch_for(packet, issues=[material_issue()]).model_dump_json()
    if place == "root":
        raw = '{"summary":"shadow",' + raw[1:]
    elif place == "coverage":
        raw = raw.replace('"coverage":{', '"coverage":{"slice_activation:M1":"supported",', 1)
    else:
        raw = raw.replace('"materiality":{', '"materiality":{"gap_kind":"explicit_conflict",', 1)
    with pytest.raises(ValueError, match="字段重复"):
        parse_batch_review(raw, packet)
    certificate = {"schema_version": protocol, "raw_arguments": raw,
                   "batch": batch_for(packet, issues=[material_issue()]).model_dump()}
    with pytest.raises(ValueError, match="字段重复"):
        parse_batch_certificate(packet, certificate)


def test_frozen_legacy_valid_certificate_and_snapshot_replay_byte_exact():
    raw = (FIXTURES / "legacy-v2-review-certificate.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "479c56dcae35297aa284b76181f8fd6c7b3f260a4b8148bdd704eabb857d2728"
    old = json.loads(raw)
    project, record = Project.model_validate(old["project"]), old["record"]
    assert "review_protocol" not in record
    packet = batch_review_packet(project, project, record)
    assert packet == old["packet"]
    assert seal_snapshot(project, project, record).model_dump() == old["snapshot"]
    assert batch_review_certificate(old["certificate"]["raw_arguments"],
                                    parse_batch_certificate(packet, old["certificate"])) == old["certificate"]
    with pytest.raises(ValueError, match="版本已更新"):
        validate_batch_certificate(project, project, record)


def test_frozen_real_invalid_v2_output_stays_invalid_without_repair():
    record = json.loads(gzip.decompress((FIXTURES / "qa55-complete-candidate.json.gz").read_bytes()))
    project = Project.model_validate(record["project"])
    before = Project.model_validate(json.loads(gzip.decompress(
        (FIXTURES / "qa55-complete-canonical.json.gz").read_bytes())))
    packet = batch_review_packet(before, project, record)
    raw = (FIXTURES / "qa55-invalid-review-output.json").read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == "b1ee33fd71d38761eab9bf3881e18334ec5b4b21f03aaaef3e19e4b08d252052"
    batch = parse_batch_review(raw, packet)
    assert batch.review_scope_hash == packet["review_scope_hash"]
    statuses = {s for values in batch.statuses.model_dump().values() for s in values}
    assert len(statuses) == 23 and len(review_subjects(project)) == 25
    assert set(packet["required_subjects"]) - statuses == {
        "slice_activation:slice-5-reminder-email", "slice_activation:slice-7-notification-reliability"}
    with pytest.raises(ValueError, match="精确覆盖"):
        normalize_batch_review(project, packet, batch)
    # Independent diagnostics on the untouched invalid batch. No corrected raw or certificate.
    catalog = set(_expanded_evidence_catalog(packet))
    assert len(batch.issues) == 5
    for issue in batch.issues:
        _validate_materiality(project, packet, issue, catalog)
        with pytest.raises(ValueError, match="具体包内记录"):
            _checked_refs(packet, issue.evidence_refs, catalog)
    v3packet = batch_review_packet(before, project, {**record, "review_protocol": SCOPED_BATCH_VERSION})
    with pytest.raises(ValueError):
        parse_batch_review(raw, v3packet)


def test_protocol_is_admitted_only_for_new_attempts_and_unknown_never_falls_back(context):
    project, record, _, old_packet = context
    record.update(turn_id="old-turn", status="needs_resolution", report={}, reviews=[])
    original = deepcopy(record)
    attempt = new_review_attempt(record, "new-turn", {"fixture": True})
    assert record == original and "review_protocol" not in record
    assert attempt["audit"]["review_protocol"] == SCOPED_BATCH_VERSION
    projected = review_projection({**record, "review_attempts": [attempt]})
    assert batch_review_packet(project, project, projected)["review_protocol"] == SCOPED_BATCH_VERSION
    # Old attempt without the field must not inherit a base v3 marker.
    del attempt["audit"]["review_protocol"]
    legacy = review_projection({**record, "review_protocol": SCOPED_BATCH_VERSION,
                                 "review_attempts": [attempt]})
    assert "review_protocol" not in legacy
    assert batch_review_packet(project, project, legacy) == old_packet
    for bad in (None, "semantic-batch/v999", ""):
        with pytest.raises(ValueError, match="未知"):
            batch_review_packet(project, project, {**record, "review_protocol": bad})
        with pytest.raises(ValueError, match="未知"):
            review_output_schema({**old_packet, "review_protocol": bad})


def test_packet_mutation_and_cross_protocol_certificate_cannot_reuse_hash(scoped, context):
    project, record, _, packet = scoped
    batch = batch_for(packet)
    certificate = batch_review_certificate(batch.model_dump_json(), batch)
    with pytest.raises(ValueError):
        parse_batch_certificate(context[3], certificate)
    value = {**record, "input": record["input"] + " New obligation."}
    changed = batch_review_packet(project, project, value)
    assert changed["candidate_hash"] == packet["candidate_hash"]
    assert changed["review_scope_hash"] != packet["review_scope_hash"]
    with pytest.raises(ValueError, match="过期"):
        normalize_batch_review(project, changed, batch)
    for subjects in (packet["required_subjects"][:-1], packet["required_subjects"] * 2):
        with pytest.raises(ValueError):
            normalize_batch_review(project, {**packet, "required_subjects": subjects}, batch)


def test_legacy_parser_semantics_are_unchanged_even_for_duplicate_keys(context):
    packet = context[3]
    raw = '{"summary":"shadow",' + batch_for(packet).model_dump_json()[1:]
    assert parse_batch_review(raw, packet) == BatchSemanticReview.model_validate_json(raw)


def test_duplicate_v3_json_is_invalid_output_not_generic_error(scoped):
    project, _, snapshot, packet = scoped
    value = batch_for(packet)
    raw = '{"summary":"shadow",' + value.model_dump_json()[1:]
    async def evaluate(_):
        batch = parse_batch_review(raw, packet)
        return normalize_batch_review(project, packet, batch), batch_review_certificate(raw, batch)
    run = asyncio.run(run_harness(snapshot, evaluate))
    assert run.executions[-1].status == "invalid_output"
    assert run.decision == "hold" and run.model_certificate_json is None
    assert run.semantic_review_json is None
