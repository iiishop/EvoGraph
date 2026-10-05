"""Validate and execute one plugin call, independently of provider framing."""

import asyncio
import json

import anyio
from pydantic import ValidationError

from .agent_errors import agent_error_event


def _plan_delta_input_failure(spec, arguments, error):
    """Describe a rejected input, without repairing it or entering its handler."""
    errors = error.errors(include_input=False, include_context=False, include_url=False)
    issues = [{
        "location": [part if isinstance(part, int) else str(part)[:80]
                     for part in item["loc"][:8]],
        "type": item["type"][:80],
        "message": item["msg"][:240],
    } for item in errors[:6]]
    invalid_json = any(item["type"] == "json_invalid" for item in errors)
    message = "规划数据格式不符合工具协议；规划内容未改动"
    if invalid_json:
        message = "规划数据的 JSON 格式无效；规划内容未改动"
        # This second parser supplies diagnostic coordinates only. Its result
        # can never be executed, normalized, unwrapped or accepted as a delta.
        try:
            json.loads(arguments)
        except json.JSONDecodeError as syntax:
            byte_offset = len(arguments[:syntax.pos].encode("utf-8"))
            issues[0]["position"] = {
                "line_1_based": syntax.lineno, "column_1_based": syntax.colno,
                "character_offset_0_based": syntax.pos,
                "utf8_byte_offset_0_based": byte_offset,
            }
            start = max(0, syntax.pos - 40)
            issues[0]["context"] = {
                "character_offset_0_based": start,
                "excerpt": arguments[start:syntax.pos + 40],
            }
            message = (f"规划数据 JSON 格式无效（第 {syntax.lineno} 行、第 {syntax.colno} 列，"
                       f"UTF-8 字节偏移 {byte_offset}）；规划内容未改动")
        except RecursionError:
            pass  # Preserve the original bounded validation issue for deep JSON.
    elif issues and issues[0]["type"] == "extra_forbidden":
        location = ".".join(str(part) for part in issues[0]["location"])[:100]
        message = f"规划数据包含未允许的字段（{location}）；规划内容未改动"
    schema = spec.parameters.model_json_schema()
    # Only the direct field/type outline is repeated; item schemas remain in
    # the advertised tool definition. No transport-envelope fields are added.
    shape = {
        "type": schema["type"],
        "additionalProperties": schema.get("additionalProperties", True),
        "properties": {key: {"type": value["type"]}
                       for key, value in schema.get("properties", {}).items()
                       if "type" in value},
        "required": schema.get("required", []),
    }
    return {
        "events": [{"type": "tool_failed", "tool": spec.name,
                    "code": "invalid_plan_delta", "label": spec.label, "message": message}],
        "progress": False,
        "payload": {
            "ok": False, "error": message,
            "code": "invalid_json" if invalid_json else "schema_mismatch",
            "issues": issues, "omitted_issue_count": max(0, len(errors) - len(issues)),
            "direct_object_shape": shape,
            "resubmit_operation": {
                "tool": spec.name, "action": "replace_rejected_call",
                "instruction": "Resubmit the complete intended delta as the direct tool-input JSON "
                               "object matching the advertised schema. Escape quotes inside JSON "
                               "strings. Do not encode the whole object as a string or put it inside "
                               "an arguments, parameters, or other wrapper. Only correct the input "
                               "format; preserve the intended planning changes. Omit unchanged "
                               "fields instead of null. No planning changes from this call were saved.",
            },
        },
    }


def _plan_manifest_input_failure(spec, arguments, error):
    """Value-free, bounded diagnostics only; never repair or execute this input."""
    errors = error.errors(include_input=False, include_context=False, include_url=False)
    # Unknown property names can themselves contain private payload text. Show
    # only protocol names and the three common misplaced source-value keys.
    names = {"changes", "kind", "id", "fields", "intent", "uses", "field",
             "quote", "source_id", "rule"}
    groups = {}
    for item in errors:
        location = [part if type(part) is int else part if part in names else "<unknown>"
                    for part in item["loc"][:8]]
        path = tuple("*" if type(part) is int else part for part in location)
        key = (path, item["type"])
        if key not in groups:
            if len(groups) >= 12:
                continue
            groups[key] = {"path": list(path), "type": item["type"], "count": 0, "locations": []}
        group = groups[key]
        group["count"] += 1
        if len(group["locations"]) < 12:
            group["locations"].append(location)
    duplicates = []
    invalid_json = any(item["type"] == "json_invalid" for item in errors)
    try:
        raw = json.loads(arguments)
    except (ValueError, RecursionError):
        raw = None
    rows = raw.get("changes") if isinstance(raw, dict) else None
    if isinstance(rows, list):
        identities = {}
        for index, row in enumerate(rows[:128]):
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("kind", "id")):
                continue
            identity = row["id"]
            if row["kind"] in {"relation", "remove_relation"}:
                try:
                    triple = json.loads(identity)
                except (ValueError, RecursionError):
                    triple = None
                if isinstance(triple, list) and len(triple) == 3 and all(isinstance(v, str) for v in triple):
                    identity = json.dumps(triple, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            identities.setdefault((row["kind"], identity), []).append(["changes", index])
        duplicates = [{"type": "duplicate_identity", "locations": locations}
                      for locations in identities.values() if len(locations) > 1]
    message = "规划清单格式不符合工具协议；规划内容未改动"
    issues = list(groups.values())
    return {
        "events": [{"type": "tool_failed", "tool": spec.name,
                    "code": "invalid_plan_manifest", "label": spec.label, "message": message}],
        "progress": False,
        "payload": {"ok": False, "error": message, "code": "invalid_plan_manifest",
                    "failure_type": "invalid_json" if invalid_json else "schema_mismatch",
                    "schema_issues": issues,
                    "omitted_issue_count": len(errors) - sum(item["count"] for item in issues),
                    "identity_issues": duplicates,
                    "identity_scan_omitted_rows": max(0, len(rows) - 128) if isinstance(rows, list) else 0,
                    "row_keys": ["kind", "id", "fields", "intent", "uses"]},
    }


async def _drain_worker(task: asyncio.Task) -> None:
    """Keep a thread's task alive until completion despite caller cancellation."""
    # AnyIO cancellation scopes repeatedly cancel unshielded checkpoints. Their
    # shield prevents a busy cancellation loop; asyncio.shield additionally
    # protects the worker from explicit, repeated Task.cancel() on this caller.
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            # Consume failures without replacing the original cancellation.
            # A handler can fail after a successful commit.
            task.exception()


class ToolExecutor:
    def __init__(self, context, registry, *, compact_snapshots=False):
        self.context = context
        self.registry = registry
        self.compact_snapshots = compact_snapshots
        self.changed = False
        self.calls_used = 0
        self.max_calls = None
        self.seen_results = set()

    async def invoke(self, name: str, arguments: str) -> dict:
        # Candidate diagnostics must not depend on a later model request copying
        # the tool exchange. Persist every attempt before parsing, including the
        # final malformed call and calls rejected by the executor registry.
        db = self.context.application.db
        attempt = db.start_tool_attempt(name, arguments) if hasattr(db, "start_tool_attempt") else None
        try:
            result = await self._invoke(name, arguments)
        except BaseException as exc:
            if attempt is not None:
                interrupted = isinstance(exc, (asyncio.CancelledError, GeneratorExit))
                try:
                    db.finish_tool_attempt(attempt, "interrupted" if interrupted else "failed",
                                           {"ok": False, "error": agent_error_event(exc)["message"]})
                except Exception as audit_error:
                    if not interrupted:
                        raise
                    # Concurrent discard closes candidate writes. Preserve the
                    # interruption instead of replacing it with an audit error.
                    exc.add_note("Interruption audit not persisted (" + type(audit_error).__name__
                                 + "); stored candidate remains authoritative")
            raise
        if attempt is not None:
            db.finish_tool_attempt(attempt, "succeeded" if result["payload"].get("ok") else "failed", result["payload"])
        return result

    async def _invoke(self, name: str, arguments: str) -> dict:
        if self.max_calls is not None and self.calls_used >= self.max_calls:
            raise ValueError("已到达本轮工具预算，候选已保留待继续")
        self.calls_used += 1
        spec = self.registry.get(name)
        try:
            if spec is None:
                raise ValueError("未知工具")
            try:
                args = spec.parameters.model_validate_json(arguments)
            except ValidationError as exc:
                if name == "submit_plan_delta":
                    return _plan_delta_input_failure(spec, arguments, exc)
                if name == "schedule_plan_changes":
                    return _plan_manifest_input_failure(spec, arguments, exc)
                raise
            mutation = spec.effect in {"created", "updated", "removed", "target"}
            task = asyncio.create_task(asyncio.to_thread(spec.handler, self.context, args))
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                # Retain the project lock until the worker has finished committing.
                await _drain_worker(task)
                # This marks a possible commit, even if the handler then failed;
                # the runtime derives the actual net changes from stored state.
                self.changed |= mutation
                raise
            if hasattr(self.context.application.db, "record_tool"):
                self.context.application.db.record_tool(name, args.model_dump(), result)
            mutation = mutation and result.get("status") != "NO_PROGRESS"
            self.changed |= mutation
            if mutation:
                event = {
                    "type": "graph_changed",
                    "view": "graph",
                    **result,
                    "label": spec.label,
                    "project": self.context.application.projects.get(
                        self.context.project_id, include_history=not self.compact_snapshots
                    ),
                }
            elif spec.effect == "question":
                event = {"type": "question", **result}
            else:
                event = {"type": "tool_finished", "label": spec.label}
            key = json.dumps([name, args.model_dump(), result], sort_keys=True, ensure_ascii=False)
            progress = key not in self.seen_results and (
                not isinstance(result, dict) or result.get("status") != "NO_PROGRESS"
            )
            self.seen_results.add(key)
            return {
                "events": [event],
                "progress": progress,
                "payload": {"ok": True, "result": result},
            }
        except (ValueError, OSError) as exc:
            return {
                "events": [
                    {
                        "type": "tool_failed",
                        "tool": name,
                        "code": "invalid_plan_delta" if name in {"submit_plan_delta", "propose_plan_patch"} else "tool_failed",
                        "label": spec.label if spec else "未知工具",
                        "message": str(exc)[:300],
                    }
                ],
                "progress": False,
                "payload": {"ok": False, "error": str(exc)[:1500]},
            }
