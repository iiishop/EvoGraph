"""Internal execution identity; legacy turns continue to own their own source."""


def intent_source_id(record):
    return (record.get("planning_job") or {}).get("source_id") or record.get("turn_id", record.get("id"))


def predecessor_schedule_owner(record):
    """Pins for a job's immediate saved schedule, never caller-supplied source IDs."""
    from .plan_units import _hash

    job = (record or {}).get("planning_job")
    schedule = (record or {}).get("work_units")
    if not job or not schedule:
        return None
    return {"candidate_id": record["id"], "record_hash": _hash(record),
            "source_id": intent_source_id(record), "schedule_hash": _hash(schedule),
            "job_id": job["job_id"]}
