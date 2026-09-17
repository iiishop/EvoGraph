"""Verification adapters register in their own module; discovery requires no switch edits."""

import importlib
import pkgutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    id: str
    label: str
    command: list[str]
    files: list[str]
    limitation: str


REGISTRY = {}


def adapter(name):
    def register(discover):
        if name in REGISTRY:
            raise ValueError(f"Duplicate verification adapter: {name}")
        REGISTRY[name] = discover
        return discover

    return register


def candidates(root, milestone):
    for module in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{module.name}")
    found = []
    for discover in REGISTRY.values():
        found.extend(discover(root, milestone))
    return found[:30]
