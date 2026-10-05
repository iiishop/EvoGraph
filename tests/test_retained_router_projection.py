"""Lossless router encoding; no omission or authority granted by alias references."""
from copy import deepcopy

import pytest
from evograph.application.plan_units import current_unit_context, initial_unit_context
from evograph.application.retained_acceptance import (
    capture_entry,
    decode_router_retained,
    project_router_retained_context,
)
from evograph.domain.plan_harness import canonical_json
from test_retained_acceptance import admitted


def context(app):
    _, db, before = admitted(app)
    raw = initial_unit_context(db)
    raw["retained_acceptance"] = decode_router_retained(raw)
    raw["retained_acceptance"].pop("entry_pins")
    # A long, exact complete same-key field; no generic text-leaf pooling.
    statement = "Exact checker acceptance. " * 40
    mechanism = "Exact read-only implementation mechanism. " * 40
    raw["acceptance_directory"][0]["statement"] = statement
    raw["completed_contract_mechanisms"] = [{"key": "check", "revision_id": "current",
                                            "mechanism": mechanism}]
    row = raw["retained_acceptance"]["assigned_claims"][0]
    row["baseline_behavior"]["statement"] = statement
    row["baseline_binding"]["mechanism"] = mechanism
    second = deepcopy(row)
    second["id"] = "draft:" + row["baseline_behavior"]["id"]
    raw["retained_acceptance"]["assigned_claims"].append(second)
    raw["retained_acceptance"]["inventory"].append({**raw["retained_acceptance"]["inventory"][0],
                                                   "id": second["id"], "origin": "staged_draft"})
    return raw, capture_entry(before), db


def test_router_round_trip_keeps_every_atom_pin_and_distinct_claim(app):
    original, entry, _ = context(app)
    saved = deepcopy(original)
    packed = project_router_retained_context(original, entry)
    assert original == saved
    assert decode_router_retained(packed) == {**original["retained_acceptance"], "entry_pins": entry["pins"]}
    assert {k: v for k, v in packed.items() if k != "retained_acceptance"} == {
        k: v for k, v in original.items() if k != "retained_acceptance"}
    assert len(canonical_json(packed).encode()) < len(canonical_json(original).encode())
    rows = packed["retained_acceptance"]["assigned_claims"]
    assert "$retained_ref" in rows[0]["baseline_behavior"]["statement"]
    assert "$retained_ref" in rows[0]["baseline_binding"]["mechanism"]
    assert "$retained_ref" in rows[-1]["baseline_binding"]
    assert len(packed["retained_acceptance"]["inventory"]) == len(rows)


@pytest.mark.parametrize("mutation", ["hash", "cross_key", "wrong_field", "forward", "cycle", "chain"])
def test_router_aliases_reject_hash_identity_type_and_cycle_changes(app, mutation):
    original, entry, _ = context(app)
    packed = project_router_retained_context(original, entry)
    rows = packed["retained_acceptance"]["assigned_claims"]
    field = rows[0]["baseline_behavior"]["statement"]
    if mutation == "hash":
        field["sha256"] = "forged"
    elif mutation == "cross_key":
        packed["acceptance_directory"][0]["key"] = "later"
    elif mutation == "wrong_field":
        field["$retained_ref"] = "/completed_contract_mechanisms/0/mechanism"
    elif mutation in {"forward", "cycle"}:
        rows[-1]["baseline_binding"]["$retained_ref"] = (
            "/retained_acceptance/assigned_claims/99/baseline_binding" if mutation == "forward"
            else f"/retained_acceptance/assigned_claims/{len(rows) - 1}/baseline_binding")
    else:
        rows[0]["baseline_binding"] = deepcopy(rows[-1]["baseline_binding"])
    with pytest.raises(ValueError):
        decode_router_retained(packed)


def test_changed_old_mechanism_remains_inline_and_current_unit_is_unencoded(app):
    original, entry, db = context(app)
    original["retained_acceptance"]["assigned_claims"][0]["baseline_binding"]["mechanism"] = "Old promise differs"
    packed = project_router_retained_context(original, entry)
    assert packed["retained_acceptance"]["assigned_claims"][0]["baseline_binding"]["mechanism"] == "Old promise differs"
    assert "record_encoding" not in current_unit_context(db)["retained_acceptance"]
    # Legacy unknown context is unchanged, not silently upgraded.
    assert project_router_retained_context({"retained_acceptance": {"status": "legacy_unknown"}}, None) == {
        "retained_acceptance": {"status": "legacy_unknown"}}
