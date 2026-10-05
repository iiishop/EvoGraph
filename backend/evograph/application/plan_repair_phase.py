"""A new user-directed ordinary phase after a complete, closed candidate.

The predecessor and its spent experiment/review budgets remain immutable audit.
Admission is not a semantic certificate, and never reopens an unfinished stage.
"""
from copy import deepcopy

from ..domain.models import Project
from ..domain.plan_contracts import candidate_hash, planning_fingerprint
from .plan_batch_policy import experiment_policy
from .plan_recheck import recheck_pins, review_projection, validate_recheck
from .plan_units import _hash, all_units_complete

REPAIR_PHASE_VERSION = "complete-candidate-repair/v1"
REPAIR_PHASE_INSTRUCTIONS = """
Repair the saved candidate according to the NEW user feedback in this turn's source.
The new feedback remains authoritative for repair scope. repair_agenda contains only validated
historical findings from the unchanged predecessor; it is additional read context, not an exhaustive
current task list or permission to ignore new feedback. Reconcile it with the new direction and saved
facts, preserving original intent and completed work. Never treat old findings or raw failed output
as a current certificate. Schedule the required changes normally and run fresh full checks afterward.
"""


def repair_phase_pins(before, record):
    return {**recheck_pins(before, record), "version": REPAIR_PHASE_VERSION,
            "candidate_fingerprint": planning_fingerprint(Project.model_validate(record["project"])),
            "base_fingerprint": planning_fingerprint(before),
            "schedule_hash": _hash(record.get("work_units")),
            "compiler_lineage_hash": _hash(record.get("compilations", []))}


def closed_turn_ids(record):
    return [record["turn_id"], *[a["id"] for a in record.get("review_attempts", [])]]


def admit_repair_phase(before, record, receipts):
    """One admission invariant, also rerun inside the candidate insert transaction."""
    validate_recheck(before, record, recheck_pins(before, record))
    if not all_units_complete(record):
        raise ValueError("新修复轮次要求完整的既有调度与检查点")
    policy = record.get("planning_experiment")
    if policy and (policy != {"project_id": before.id, **experiment_policy(policy["version"])}
                   or record.get("metrics", {}).get("planning_experiment") != policy):
        raise ValueError("既有实验来源或预算已改变")
    turns = closed_turn_ids(record)
    audits = [record, *[a["audit"] for a in record.get("review_attempts", [])]]
    if (len(receipts) != len(turns) or any(not a.get("closed_at") for a in record.get("review_attempts", []))
            or any(c.get("status") in {"admitted", "streaming", "running"}
                   for audit in audits for c in audit.get("metrics", {}).get("calls", []))):
        raise ValueError("既有生成或评审轮次尚未结束")
    for turn, receipt in zip(turns, receipts):
        outcome = receipt.get("candidate_outcome", {})
        if (receipt.get("turn_id") != turn or receipt.get("status") not in {"completed", "failed", "stopped"}
                or outcome.get("id") != record["id"] or outcome.get("canonical_unchanged") is not True):
            raise ValueError("新修复轮次需要既有轮次的准确结束凭据")
    return {"version": REPAIR_PHASE_VERSION, "pins": repair_phase_pins(before, record),
            "predecessor_experiment": deepcopy(policy),
            "closed_turns": [{"turn_id": turn, "receipt_hash": _hash(receipt)}
                             for turn, receipt in zip(turns, receipts)],
            "feedback_kind": "new_user_source", "prior_budgets": "closed_not_reused"}


def validated_repair_agenda(before, record):
    """Only replayable accepted findings become automatic repair instructions."""
    from .plan_repair_context import explicit_repair_agenda
    current = review_projection(record)
    if not current.get("batch_reviews"):
        return None
    try:
        return explicit_repair_agenda(before, current)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def validate_repair_start(record, predecessor):
    """The initial state is the exact saved plan plus one attributed feedback source."""
    old = Project.model_validate(predecessor["project"])
    new = Project.model_validate(record["project"])
    sources = new.plan_contract.sources
    if (not sources or sources[:-1] != old.plan_contract.sources
            or sources[-1].id != record["id"] or record["turn_id"] != record["id"]
            or record["resumes_candidate_id"] != predecessor["id"]
            or sources[-1].text != record["input"] or sources[-1].origin != "user"
            or sources[-1].reference_context != record["reference_context"]
            or sources[-1].evidence or sources[-1].activity or sources[-1].message_id
            or record.get("planning_experiment") or record["metrics"].get("planning_experiment")
            or record["metrics"]["provider_calls"] != 0 or record.get("work_units")
            or record.get("reviews") or record.get("batch_reviews")
            or record["prior_work_units"] != predecessor["work_units"]
            or record["generation_progress"] != {"state": "staged", "checkpoint_count":
                predecessor["generation_progress"]["checkpoint_count"]}):
        raise ValueError("新修复轮次必须保留原候选与检查点，并准确归属新反馈")
    new.plan_contract.sources = sources[:-1]
    if candidate_hash(new) != candidate_hash(old) or new != old:
        raise ValueError("新修复轮次不得改写既有候选")
