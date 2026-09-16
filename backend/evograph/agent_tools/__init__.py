"""Drop-in trusted tool plugins; no central tool-name dispatch switch."""

from importlib import import_module
from pkgutil import iter_modules

from .base import REGISTRY


def tools():
    for module in iter_modules(__path__):
        if module.name != "base" and not module.name.startswith("_"):
            import_module(f"{__name__}.{module.name}")
    return REGISTRY
