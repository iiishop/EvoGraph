"""Fixed entry claims, projected from pinned records; compiler audits own changes.

This module checks identity, provenance and availability, never language meaning
or user authorization. There is no parallel ledger or inferred requirement DSL.
"""
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.dependencies import ancestor_sets
from ..domain.models import Project
from ..domain.plan_contracts import active_behaviors, candidate_hash
from ..domain.plan_harness import canonical_json, content_hash

VERSION = "retained-acceptance/v1"


class AcceptanceChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    baseline_id: str = Field(min_length=1, max_length=140)
    disposition: Literal["retained", "replaced", "retired"]
    witness_keys: list[str] = Field(max_length=16)
    acceptance_at: str = Field(default="", max_length=40)
    kind: Literal["preserving_refactor", "user_scope_change", "draft_correction"]
    reason: str = Field(default="", max_length=240)
    source_id: str = Field(default="", max_length=100)
    quote: str = Field(default="", max_length=500)

    @field_validator("*", mode="before")
    @classmethod
    def no_null(cls, value):
        if value is None:
            raise ValueError("omit unchanged acceptance metadata instead of null")
        return value


def _history_pins(project):
    # Existing project histories remain the text authority. These bounded hashes
    # prevent staged provenance from disappearing outside the canonical history.
    return {**{"source:" + row.id: content_hash(row.model_dump(exclude={"activity", "message_id"}))
               for row in project.plan_contract.sources},
            **{"requirement:" + row.id: content_hash(row.model_dump(include={"id", "source_id", "quote", "origin", "kind"}))
               for row in project.plan_contract.requirements},
            **{"process:" + row.source_id + ":" + row.id: content_hash(row.model_dump())
               for row in project.plan_contract.process_constraints}}


def capture_entry(before, staged_record=None, *, source_id="", audit_start=0):
    """Called by admission using durable records, never a model-selected inventory."""
    pins = {"project_id": before.id, "canonical_hash": candidate_hash(before),
            "canonical_revision": before.revision, "source_id": source_id,
            "staged_candidate_id": None, "staged_candidate_hash": None, "staged_record_hash": None}
    projects = [("canonical_accepted", before)]
    if staged_record is not None:
        staged = Project.model_validate(staged_record["project"])
        if staged.id != before.id or staged_record["base_revision"] > before.revision:
            raise ValueError("retained acceptance entry project/base mismatch")
        pins.update(staged_candidate_id=staged_record["id"],
                    staged_candidate_hash=candidate_hash(staged),
                    staged_record_hash=content_hash(staged_record))
        projects.append(("staged_draft", staged))
    claims = []
    for origin, project in projects:
        bindings = {b.behavior_key: b for b in project.plan_contract.bindings}
        for key, behavior in sorted(active_behaviors(project).items()):
            binding = bindings.get(key)
            if binding is None or binding.behavior_revision_id != behavior.id:
                raise ValueError("retained acceptance entry binding unavailable: " + key)
            claims.append({"id": ("canonical:" if origin == "canonical_accepted" else "draft:")
                           + behavior.id, "origin": origin, "key": key,
                           "behavior_id": behavior.id, "behavior_hash": content_hash(behavior.model_dump()),
                           "binding": binding.model_dump()})
    history_pins = {}
    for _, project in projects:
        for key, value in _history_pins(project).items():
            if key in history_pins and history_pins[key] != value:
                raise ValueError("retained acceptance entry history conflicts: " + key)
            history_pins[key] = value
    payload = {"version": VERSION, "pins": pins, "claims": claims, "audit_start": audit_start,
               "history_pins": history_pins}
    if staged_record is not None and staged_record.get("retained_acceptance"):
        previous_entry = entry_for(before, staged_record)
        previous_claims = {row["id"]: row for row in previous_entry["claims"]}
        prior = resolutions(previous_entry, staged_record, staged)
        inherited = {}
        for claim in claims:
            if claim["origin"] != "canonical_accepted":
                continue
            if previous_claims.get(claim["id"]) != claim:
                raise ValueError("retained acceptance canonical predecessor identity changed")
            resolution = prior[claim["id"]]
            if resolution["kind"] != "unchanged":
                inherited[claim["id"]] = {**deepcopy(resolution), "inherited_proposal": True}
        if inherited:
            # A bounded projection of exact predecessor audit results, not an
            # independent mutable ledger or a carried semantic approval.
            payload["inherited_canonical_dispositions"] = inherited
    return {**payload, "entry_hash": content_hash(payload)}


def entry_for(before, record):
    entry = record.get("retained_acceptance")
    if entry is None:
        if record.get("resumes_candidate_id") or record.get("planning_job"):
            raise ValueError("retained acceptance legacy entry is unknown; exact pinned admission required")
        # Standalone legacy/canonical snapshots are mechanically scoped. Durable
        # new candidates always carry an admission checked under the writer lock.
        entry = capture_entry(before)
    payload = {k: v for k, v in entry.items() if k != "entry_hash"}
    if entry.get("version") != VERSION or content_hash(payload) != entry.get("entry_hash"):
        raise ValueError("retained acceptance entry seal changed")
    if entry["pins"]["project_id"] != before.id or entry["pins"]["canonical_hash"] != candidate_hash(before):
        raise ValueError("retained acceptance canonical pin is stale")
    canonical = capture_entry(before)["claims"]
    if [r for r in entry["claims"] if r["origin"] == "canonical_accepted"] != canonical:
        raise ValueError("retained acceptance canonical inventory missing or changed")
    ids = [r["id"] for r in entry["claims"]]
    if len(ids) != len(set(ids)) or len(ids) > 256:
        raise ValueError("retained acceptance duplicate or oversized entry inventory")
    return entry


def _rows(raw):
    removals = raw.get("removal_acceptance_changes", {})
    if set(removals) - set(raw.get("remove_contract_keys", [])):
        raise ValueError("removal acceptance metadata requires its explicit remove_contract_keys identity")
    for row in raw.get("contracts", []):
        for value in row.get("acceptance_changes", []):
            yield row["key"], value, row.get("owner_change_reason", "")
    for key, values in removals.items():
        if len(values) > 16:
            raise ValueError("retained acceptance removal mapping exceeds bounded rows")
        for value in values:
            yield key, value, ""


def resolutions(entry, record, candidate):
    """Replay only affected mappings from the existing immutable compiler audit."""
    current_history = _history_pins(candidate)
    if any(current_history.get(key) != value for key, value in entry["history_pins"].items()):
        raise ValueError("retained acceptance original source/requirement history missing or changed")
    history = {b.id: b for b in candidate.behaviors}
    claims = {r["id"]: r for r in entry["claims"]}
    result = {}
    for identity, row in claims.items():
        behavior = history.get(row["behavior_id"])
        if behavior is None or content_hash(behavior.model_dump()) != row["behavior_hash"]:
            raise ValueError("retained acceptance original behavior history missing or changed: " + identity)
        result[identity] = {"baseline_id": identity, "disposition": "retained", "holder": row["key"],
                            "witness_keys": [row["key"]], "acceptance_at": behavior.owner,
                            "kind": "unchanged", "reason": "", "source_id": "", "quote": ""}
    for identity, proposed in entry.get("inherited_canonical_dispositions", {}).items():
        if identity not in claims or claims[identity]["origin"] != "canonical_accepted":
            raise ValueError("retained acceptance inherited disposition has no canonical identity")
        result[identity] = deepcopy(proposed)
    audits = record.get("compilations", [])
    start = entry["audit_start"]
    if type(start) is not int or start < 0 or start > len(audits):
        raise ValueError("retained acceptance compiler audit prefix missing")
    sources = {s.id: s.text for s in candidate.plan_contract.sources}
    for audit in audits[start:]:
        rows = list(_rows(audit.get("ir", {})))
        if not rows:
            continue
        if content_hash({k: v for k, v in audit.items() if k != "audit_hash"}) != audit.get("audit_hash"):
            raise ValueError("retained acceptance compiler audit seal changed")
        seen = set()
        for holder, raw, owner_reason in rows:
            change = AcceptanceChange.model_validate(raw).model_dump()
            identity = change["baseline_id"]
            if identity not in claims or identity in seen:
                raise ValueError("retained acceptance missing, forged or duplicate baseline ID")
            seen.add(identity)
            old, claim = result[identity], claims[identity]
            if holder not in {claim["key"], old["holder"]}:
                raise ValueError("retained acceptance baseline is not writable through this contract")
            reason = change["reason"] or owner_reason
            if not reason.strip():
                raise ValueError("retained acceptance change requires reason or owner_change_reason")
            witnesses = change["witness_keys"]
            if len(witnesses) != len(set(witnesses)) or any(not k.strip() for k in witnesses):
                raise ValueError("retained acceptance witnesses must be unique nonblank keys")
            retired = change["disposition"] == "retired"
            if bool(witnesses) == retired:
                raise ValueError("retained acceptance retired has no witnesses; other dispositions require witnesses")
            if change["kind"] == "draft_correction" and claim["origin"] != "staged_draft":
                raise ValueError("retained acceptance canonical claim cannot be corrected as an unapproved draft")
            if retired and change["kind"] == "preserving_refactor":
                raise ValueError("retained acceptance retirement needs scope change or draft correction")
            if change["kind"] in {"user_scope_change", "draft_correction"}:
                if (not change["source_id"] or not change["quote"].strip()
                        or change["quote"] not in sources.get(change["source_id"], "")):
                    raise ValueError("retained acceptance change requires exact source provenance; not authorization")
            elif change["source_id"] or change["quote"]:
                if not change["quote"].strip() or change["quote"] not in sources.get(change["source_id"], ""):
                    raise ValueError("retained acceptance source quote unavailable")
            result[identity] = {**change, "reason": reason,
                                "acceptance_at": change["acceptance_at"] or old["acceptance_at"],
                                "holder": witnesses[0] if witnesses else holder}
    return result


def writable_ids(record, project, keys):
    entry = record.get("retained_acceptance")
    if entry is None:
        return []
    current = resolutions(entry, record, project)
    return [row["id"] for row in entry["claims"]
            if row["key"] in keys or current[row["id"]]["holder"] in keys]


def disposition_only_contract(record, project, key, fields):
    return (set(fields) == {"acceptance_changes"}
            and bool(writable_ids(record or {}, project, {key})))


def changed_disposition_only(record, project, raw, unit):
    """True only for an explicitly assigned, genuinely changed audit-only unit."""
    if (not unit["changes"] or set(raw) - {"summary", "contracts"}
            or any(row["kind"] != "contract" or row["fields"] != ["acceptance_changes"]
                   for row in unit["changes"])
            or not raw.get("contracts")
            or any(set(row) != {"key", "acceptance_changes"} or not row["acceptance_changes"]
                   for row in raw["contracts"])):
        return False
    entry = record.get("retained_acceptance")
    if entry is None:
        return False
    prior = {**record, "compilations": record.get("compilations", [])[:-1]}
    old, new = resolutions(entry, prior, project), resolutions(entry, record, project)
    def substantive(value):
        return {key: item for key, item in value.items() if key != "inherited_proposal"}
    if any(not any(substantive(old[item["baseline_id"]]) != substantive(new[item["baseline_id"]])
                   for item in row["acceptance_changes"]) for row in raw["contracts"]):
        raise ValueError("acceptance-only unit repeats an unchanged disposition")
    return True


def validate_metadata_scope(record, project, raw):
    """Used before compile and again by transactional checkpoint admission."""
    entry = record.get("retained_acceptance")
    rows = list(_rows(raw))
    if rows and entry is None:
        raise ValueError("retained acceptance metadata has no server-pinned baseline")
    seen = set()
    for holder, row, _ in rows:
        change = AcceptanceChange.model_validate(row)
        if change.baseline_id in seen or change.baseline_id not in writable_ids(record, project, {holder}):
            raise ValueError("retained acceptance baseline ID is duplicate, stale or outside assigned holder")
        seen.add(change.baseline_id)
    # A slice deletion cannot silently swallow an entry contract's metadata.
    active = active_behaviors(project)
    removed_slices = set(raw.get("remove_slice_ids", []))
    for key, behavior in active.items():
        if behavior.owner in removed_slices and writable_ids(record, project, {key}):
            if key not in raw.get("remove_contract_keys", []):
                raise ValueError("retained acceptance slice deletion requires explicit contained contract removal")


def projection(before, candidate, record):
    entry = entry_for(before, record)
    current = resolutions(entry, record, candidate)
    history = {b.id: b for b in candidate.behaviors}
    active = active_behaviors(candidate)
    bindings = {b.behavior_key: b for b in candidate.plan_contract.bindings}
    ancestors = ancestor_sets({m.id: m.dependencies for m in [*candidate.source_milestones, *candidate.milestones]})
    rows = []
    for claim in entry["claims"]:
        change = current[claim["id"]]
        witnesses = []
        for key in change["witness_keys"]:
            behavior, binding = active.get(key), bindings.get(key)
            if behavior is None or binding is None or binding.behavior_revision_id != behavior.id:
                raise ValueError("retained acceptance active witness missing or stale: " + key)
            stage = change["acceptance_at"]
            if stage not in ancestors or behavior.owner not in {stage, *ancestors[stage]}:
                raise ValueError("retained acceptance witness unavailable at promised stage: " + key)
            witnesses.append({"key": key, "behavior_id": behavior.id, "owner": behavior.owner})
        original = history[claim["behavior_id"]].model_dump()
        baseline_binding = claim["binding"]
        # Removing/renaming a modeled declaration must not manufacture coverage.
        if witnesses and (baseline_binding.get("steps") or baseline_binding.get("provides")):
            if not any(bindings[w["key"]].steps for w in witnesses):
                raise ValueError("retained acceptance typed declaration lost across witnesses")
            modeled = {p["key"] for p in baseline_binding.get("provides", []) if p.get("consumes") is not None}
            present = {p.key for w in witnesses for p in bindings[w["key"]].provides if p.consumes is not None}
            if modeled - present:
                raise ValueError("retained acceptance modeled consumption lost across witnesses")
        rows.append({"id": claim["id"], "origin": claim["origin"], "baseline_behavior": original,
                     "baseline_binding": deepcopy(baseline_binding), "resolution": change,
                     "witnesses": witnesses})
    return {"version": VERSION, "entry_hash": entry["entry_hash"], "pins": entry["pins"], "claims": rows}


def subjects(projected):
    return ["retained_acceptance:" + row["id"] for row in projected["claims"]]


def generation_context(record, project, keys=None):
    """Complete fixed inventory; exact original evidence for assigned holders."""
    entry = record.get("retained_acceptance")
    if entry is None:
        return {"status": "legacy_unknown"}
    current = resolutions(entry, record, project)
    history = {b.id: b.model_dump() for b in project.behaviors}
    selected = set(writable_ids(record, project, keys)) if keys is not None else set(current)
    return {"entry_hash": entry["entry_hash"],
            "inventory": [{"id": r["id"], "origin": r["origin"], "key": r["key"],
                           "acceptance_at": current[r["id"]]["acceptance_at"],
                           "holder": current[r["id"]]["holder"],
                           "disposition": current[r["id"]]["disposition"]} for r in entry["claims"]],
            "assigned_claims": [{"id": r["id"], "baseline_behavior": history[r["behavior_id"]],
                                 "baseline_binding": deepcopy(r["binding"]),
                                 "resolution": current[r["id"]]} for r in entry["claims"] if r["id"] in selected],
            "coverage": "Fixed request-entry claims; canonical accepted and staged drafts are distinct. "
                        "Intermediate inventions never join this inventory. Exact original evidence is shown "
                        "for assigned holders; other claims remain enforced and independently reviewed. "
                        "Changed prose/helpers are not automatically scope loss. Source quotes prove provenance, "
                        "not permission. Witness references grant no write authority. Optional typing remains unmodeled."}


_ROUTER_REF = "$retained_ref"
_ROUTER_ENCODING = {
    "version": "router-retained-records/v1",
    "meaning": "At retained baseline records or their statement/mechanism fields, $retained_ref "
               "resolves the exact complete value at its JSON pointer; sha256 binds decoded content. "
               "Field aliases share equal same-key text with acceptance_directory or "
               "completed_contract_mechanisms. Record aliases reuse an earlier equal same-kind retained "
               "record. Original IDs, origins and dispositions stay distinct; shared bytes prove no semantics.",
}


def decode_router_retained(context):
    """Decode only the two named record kinds and two full-text field positions."""
    retained = context["retained_acceptance"]
    if retained.get("record_encoding") != _ROUTER_ENCODING:
        raise ValueError("retained router encoding unavailable")
    rows = retained["assigned_claims"]
    keys = {row["id"]: row["key"] for row in retained["inventory"]}

    def decode(value, index, kind, field=None):
        key = keys[rows[index]["id"]]
        if not isinstance(value, dict) or _ROUTER_REF not in value:
            if field is not None:
                if not isinstance(value, str):
                    raise ValueError("retained router text must be complete string")
                return value
            if not isinstance(value, dict) or value.get("behavior_key") != key:
                raise ValueError("retained router record type/identity changed")
            text_field = "statement" if kind == "baseline_behavior" else "mechanism"
            return {**deepcopy(value), text_field: decode(value[text_field], index, kind, text_field)}
        if set(value) != {_ROUTER_REF, "sha256"} or not isinstance(value[_ROUTER_REF], str):
            raise ValueError("retained router alias shape changed")
        parts = value[_ROUTER_REF].split("/")
        if not parts or parts[0] != "":
            raise ValueError("retained router alias is not a local pointer")
        if field is not None:
            group = "acceptance_directory" if field == "statement" else "completed_contract_mechanisms"
            if (len(parts) != 4 or parts[1] != group or parts[3] != field
                    or not parts[2].isdigit() or str(int(parts[2])) != parts[2]):
                raise ValueError("retained router text alias must target its exact same-kind directory field")
            try:
                target = context[group][int(parts[2])]
            except (IndexError, KeyError, TypeError):
                raise ValueError("retained router text target missing") from None
            result = target.get(field)
            if target.get("key") != key or not isinstance(result, str):
                raise ValueError("retained router text target type/identity changed")
        else:
            if (len(parts) != 5 or parts[1:3] != ["retained_acceptance", "assigned_claims"]
                    or parts[4] != kind or not parts[3].isdigit() or str(int(parts[3])) != parts[3]
                    or not 0 <= int(parts[3]) < index):
                raise ValueError("retained router record alias must target an earlier same-kind record")
            target_index = int(parts[3])
            target = rows[target_index][kind]
            if keys[rows[target_index]["id"]] != key or _ROUTER_REF in target:
                raise ValueError("retained router record aliases cannot chain or change identity")
            result = decode(target, target_index, kind)
        if content_hash(result) != value["sha256"]:
            raise ValueError("retained router alias content hash changed")
        return result

    return {**{key: deepcopy(value) for key, value in retained.items() if key != "record_encoding"},
            "assigned_claims": [{**deepcopy(row),
                **{kind: decode(row[kind], i, kind) for kind in ("baseline_behavior", "baseline_binding")}}
                for i, row in enumerate(rows)]}


def project_router_retained_context(context, entry):
    """Lossless router-only encoding. Unit generators keep their exact scoped rows."""
    if entry is None:
        return context
    result = deepcopy(context)
    retained = result["retained_acceptance"]
    original = {**deepcopy(retained), "entry_pins": deepcopy(entry["pins"])}
    retained["entry_pins"] = deepcopy(entry["pins"])
    retained["record_encoding"] = deepcopy(_ROUTER_ENCODING)
    originals = original["assigned_claims"]
    for index, row in enumerate(retained["assigned_claims"]):
        for kind, field, group in (("baseline_behavior", "statement", "acceptance_directory"),
                                   ("baseline_binding", "mechanism", "completed_contract_mechanisms")):
            record = originals[index][kind]
            # Equal complete records may share bytes but keep distinct claim IDs.
            prior = next((i for i in range(index) if originals[i][kind] == record
                          and _ROUTER_REF not in retained["assigned_claims"][i][kind]), None)
            if prior is not None:
                alias = {_ROUTER_REF: f"/retained_acceptance/assigned_claims/{prior}/{kind}",
                         "sha256": content_hash(record)}
                if len(canonical_json(alias).encode()) < len(canonical_json(record).encode()):
                    row[kind] = alias
                    continue
            target = next((i for i, current in enumerate(result[group])
                           if current["key"] == record["behavior_key"] and current[field] == record[field]), None)
            if target is not None:
                alias = {_ROUTER_REF: f"/{group}/{target}/{field}",
                         "sha256": content_hash(record[field])}
                if len(canonical_json(alias).encode()) < len(canonical_json(record[field]).encode()):
                    row[kind][field] = alias
    if decode_router_retained(result) != original:
        raise ValueError("retained router encoding did not preserve every original claim atom")
    return result
