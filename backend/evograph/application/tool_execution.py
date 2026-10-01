"""Validate and execute one plugin call, independently of provider framing."""

import asyncio
import json

import anyio


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
    def __init__(self, context, registry):
        self.context = context
        self.registry = registry
        self.changed = False
        self.calls_used = 0
        self.seen_results = set()

    async def invoke(self, name: str, arguments: str) -> dict:
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
            mutation = mutation and result.get("status") != "NO_PROGRESS"
            self.changed |= mutation
            if mutation:
                event = {
                    "type": "graph_changed",
                    "view": "graph",
                    **result,
                    "label": spec.label,
                    "project": self.context.application.projects.get(self.context.project_id),
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
                        "label": spec.label if spec else "未知工具",
                        "message": str(exc)[:300],
                    }
                ],
                "progress": False,
                "payload": {"ok": False, "error": str(exc)[:1500]},
            }
