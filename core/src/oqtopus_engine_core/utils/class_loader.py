from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType


def load_class(path: str) -> type[Any]:
    """Dynamically load a class from a module path string.

    Args:
        path: Fully qualified class path (e.g. "mypackage.module.MyClass").

    Returns:
        The class object referenced by the path.

    """
    module_path, class_name = path.rsplit(".", 1)
    module: ModuleType = importlib.import_module(module_path)
    cls: type[Any] = getattr(module, class_name)
    return cls
