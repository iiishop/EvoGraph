"""Validate and execute one plugin call, independently of provider framing."""

import asyncio
import json


class ToolExecutor:
    def __init__(self, context, registry):
        self.context = context
        self.registry = registry
        self.changed = False
        self.calls_used = 0
        self.seen_results = set()

    async def invoke(self, name: str, arguments: str) -> dict:
        if self.calls_used >= 24:
            raise ValueError("本轮工具预算已用完；已完成的修改已保留")
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
                await task
                self.changed |= mutation
                raise
            self.changed |= mutation
            if mutation:
                event = {
                    "type": "graph_changed", **result, "label": spec.label,
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
            return {"events": [event], "progress": progress, "payload": {"ok": True, "result": result}}
        except (ValueError, OSError) as exc:
            return {
                "events": [{"type": "tool_failed", "label": spec.label if spec else "未知工具", "message": str(exc)[:300]}],
                "progress": False, "payload": {"ok": False, "error": str(exc)[:1500]},
            }
