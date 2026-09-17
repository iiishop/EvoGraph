from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

REGISTRY = {}


@dataclass
class ToolContext:
    project_id: str
    application: object
    inspected: set[str] = field(default_factory=set)
    receipts: dict[str, str] = field(default_factory=dict)
    verification_milestone: str | None = None
    checks_run: int = 0
    visual_attachments: set[str] = field(default_factory=set)
    web_cache: dict[str, dict] = field(default_factory=dict)
    paused: bool = False


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: type[BaseModel]
    handler: Callable
    label: str
    effect: str
    focus_field: str | None

    def schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters.model_json_schema(),
            },
        }


def tool(
    name: str,
    description: str,
    parameters: type[BaseModel],
    *,
    label: str,
    effect: str = "inspect",
    focus_field: str | None = None,
):
    def decorate(handler):
        if name in REGISTRY:
            raise ValueError(f"Duplicate tool registration: {name}")
        REGISTRY[name] = ToolSpec(
            name, description, parameters, handler, label, effect, focus_field
        )
        return handler

    return decorate
