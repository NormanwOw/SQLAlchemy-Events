import importlib
import pkgutil
from pathlib import Path
from types import ModuleType
from typing import Iterable, List


def autodiscover(paths: Iterable[str]) -> List[ModuleType]:
    modules: List[ModuleType] = []

    for path in paths:
        if isinstance(path, Path):
            path = str(path)
            path = path.replace('\\', '.')

        path = path.strip('/').replace('/', '.').lstrip('.')
        module = importlib.import_module(path)
        modules.append(module)

        if not hasattr(module, '__path__'):
            continue

        for _, module_name, _ in pkgutil.walk_packages(
            module.__path__,
            module.__name__ + '.',
        ):
            submodule = importlib.import_module(module_name)
            modules.append(submodule)

    return modules