"""Built-in adapter discovery. Add one module exporting a decorated adapter."""

from importlib import import_module
from pkgutil import iter_modules

from .base import REGISTRY


def adapters():
    if not REGISTRY:
        for module in iter_modules(__path__):
            if not module.name.startswith("_") and module.name != "base":
                import_module(f"{__name__}.{module.name}")
    return REGISTRY
