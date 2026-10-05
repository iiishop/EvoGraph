"""Exact unfinished predecessor intents for explicitly selected routing contexts.

This read-only projection is not a requirement source, schedule repair or gate.
Do not call it from ordinary generator contexts or walk older candidate ancestry.
"""
from collections import Counter
from copy import deepcopy

from .plan_phase import intent_source_id

_UNIT_FIELDS = ("id", "hash", "state", "contract_count", "existing_text_bytes",
                "estimated_text_bytes", "depends_on", "holds")
_PROVENANCE_FIELDS = ("version", "project_id", "source_id", "origin_source_id",
                      "expected_revision", "expected_fingerprint")


def _current_completions(record, prior):
    current = record.get("work_units")
    if not isinstance(current, dict) or any((
        current.get("project_id") != prior.get("project_id"),
        current.get("source_id") != intent_source_id(record),
        not prior.get("origin_source_id"),
        current.get("origin_source_id") != prior.get("origin_source_id"),
        current.get("manifest_hash") != prior.get("manifest_hash"),
    )):
        return set()
    checkpoints = {(c.get("unit_id"), c.get("unit_hash"))
                   for c in current.get("checkpoints", [])}
    return {(u["id"], u["hash"]) for u in current.get("units", [])
            if u.get("state") == "completed" and u["id"] in current.get("completed_ids", [])
            and (u["id"], u["hash"]) in checkpoints}


def _group_architecture_atoms(changes):
    """Losslessly factor same-origin field atoms in this read-only unit view.

    Only the exact server-owned atom shape is grouped. Unknown shapes and
    separate origins remain untouched; this is never an executable manifest.
    """
    groups = {}
    required = {"kind", "id", "change_id", "fields", "uses", "intent",
                "origin_change_id", "origin_change_hash"}
    for change in changes:
        if (set(change) != required or change["kind"] != "architecture"
                or change["id"] != "architecture" or not isinstance(change["fields"], list)
                or len(change["fields"]) != 1 or not isinstance(change["fields"][0], str)
                or change["origin_change_id"] != "architecture:architecture"
                or not isinstance(change["origin_change_hash"], str) or not change["origin_change_hash"]
                or change["change_id"] != change["origin_change_id"] + "#" + change["fields"][0]
                or not isinstance(change["uses"], list)
                or any(not isinstance(use, dict) or use.get("field") != change["fields"][0]
                       for use in change["uses"])):
            continue
        groups.setdefault(change["origin_change_hash"], []).append(change)
    grouped = {}
    for rows in groups.values():
        fields = [row["fields"][0] for row in rows]
        positions = [i for i, row in enumerate(changes) if any(row is item for item in rows)]
        if (len(rows) < 2 or len(set(fields)) != len(fields)
                or positions != list(range(positions[0], positions[-1] + 1))):
            continue
        first = rows[0]
        item = {key: deepcopy(first[key]) for key in
                ("kind", "id", "origin_change_id", "origin_change_hash")}
        item.update(fields=fields, uses=[deepcopy(use) for row in rows for use in row["uses"]],
                    pending_atom_ids=[row["change_id"] for row in rows])
        if all(row["intent"] == first["intent"] for row in rows):
            item["intent"] = first["intent"]
        else:
            item["field_intents"] = [{"field": row["fields"][0], "intent": row["intent"]}
                                     for row in rows]
        for row in rows:
            grouped[id(row)] = item if row is first else None
    return [grouped.get(id(row), row) for row in changes
            if id(row) not in grouped or grouped[id(row)] is not None]


def prior_pending_intent_context(record, project_id):
    """Return an enriched prior summary without mutating record or its schedules.

    The caller must explicitly opt in only for routing. ``prior_work_units`` is
    the immediate predecessor's own schedule, as supplied by the resume path.
    A matching current checkpoint may finish a prior atom, but a different
    manifest or lineage never silently cancels a previous model proposal.
    """
    prior = record.get("prior_work_units")
    if not isinstance(prior, dict) or prior.get("project_id") != project_id:
        return None
    source_id = record.get("resumes_candidate_id")
    owner = record.get("prior_work_units_owner")
    if owner is not None:
        from .plan_units import _hash

        if (not isinstance(owner, dict)
                or set(owner) != {"candidate_id", "record_hash", "source_id", "schedule_hash", "job_id"}
                or owner["candidate_id"] != record.get("resumes_candidate_id")
                or owner["schedule_hash"] != _hash(prior)):
            return None
        source_id = owner["source_id"]
    if prior.get("source_id") != source_id:
        return None
    completed = _current_completions(record, prior)
    pending = [u for u in prior.get("units", [])
               if u["id"] in prior.get("pending_ids", []) and u.get("state") != "completed"]
    remaining = [u for u in pending if (u["id"], u["hash"]) not in completed]
    if not remaining:
        return None
    result = {"manifest_hash": prior["manifest_hash"],
              "completed_ids": list(prior["completed_ids"]),
              "pending_ids": list(prior["pending_ids"]),
              "units": [{k: deepcopy(u[k]) for k in _UNIT_FIELDS} for u in prior["units"]],
              **{k: deepcopy(prior[k]) for k in _PROVENANCE_FIELDS}}
    changes = {u["id"]: deepcopy(u["changes"]) for u in remaining}
    counts = Counter(c["intent"] for rows in changes.values() for c in rows)
    texts = list(dict.fromkeys(c["intent"] for rows in changes.values() for c in rows
                              if counts[c["intent"]] > 1))
    for unit in result["units"]:
        if unit["id"] not in changes:
            continue
        unit["changes"] = _group_architecture_atoms(changes[unit["id"]])
        intents = [intent for change in unit["changes"]
                   for intent in change.get("field_intents", [change])]
        for change in intents:
            if change["intent"] in texts:
                change["intent_ref"] = texts.index(change.pop("intent"))
    if texts:
        result["intent_texts"] = texts
    finished = [u["id"] for u in pending if (u["id"], u["hash"]) in completed]
    if finished:
        current = record["work_units"]
        result["completed_in_current"] = {
            **{k: deepcopy(current[k]) for k in _PROVENANCE_FIELDS},
            "manifest_hash": current["manifest_hash"], "unit_ids": finished,
        }
    result["pending_changes_note"] = (
        "Exact unfinished model-planned changes at this prior snapshot, not binding requirements. "
        "Reconcile with current saved facts and the latest user direction, which may supersede them. "
        "intent_ref indexes exact text in intent_texts. Grouped architecture fields retain ordered "
        "pending_atom_ids and origin hash; each field keeps its exact shared or field_intents text "
        "and uses. These read-only groups do not change the saved units. Completed prior work and matching current "
        "checkpoint completions are excluded from changes; prior counts/states remain historical."
    )
    return result
