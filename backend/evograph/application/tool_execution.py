"""Validate and execute one plugin call, independently of provider framing."""

import asyncio
import json

import anyio

from .agent_errors import agent_error_event


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
                db.finish_tool_attempt(attempt, "interrupted" if isinstance(exc, (asyncio.CancelledError, GeneratorExit)) else "failed",
                                       {"ok": False, "error": agent_error_event(exc)["message"]})
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
            args = spec.parameters.model_validate_json(arguments)
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
