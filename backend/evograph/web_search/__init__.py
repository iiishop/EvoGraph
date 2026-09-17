"""Provider registry inspired by Hermes: capability tools are independent of vendors."""

import importlib
import pkgutil

REGISTRY = {}


def register(cls):
    if cls.descriptor["id"] in REGISTRY:
        raise ValueError("Duplicate web provider")
    REGISTRY[cls.descriptor["id"]] = cls()
    return cls


def providers():
    for module in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{module.name}")
    return REGISTRY
