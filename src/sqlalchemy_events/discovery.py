import importlib
import pkgutil
from pathlib import Path
from types import ModuleType
from typing import Iterable, List


def autodiscover(paths: Iterable[str | Path]) -> List[ModuleType]:
    modules: List[ModuleType] = []
    seen: set[str] = set()

    def add_module(module: ModuleType):
        if module.__name__ in seen:
            return
        seen.add(module.__name__)
        modules.append(module)

    for path in paths:
        is_file = False

        if isinstance(path, Path):
            is_file = path.suffix == '.py'
            path = str(path)

        if isinstance(path, str) and path.endswith('.py'):
            is_file = True
            path = path[:-3]

        path = path.replace('\\', '.').replace('/', '.').lstrip('.')

        module = importlib.import_module(path)
        add_module(module)

        if is_file or not hasattr(module, '__path__'):
            continue

        for _, module_name, _ in pkgutil.walk_packages(
            module.__path__,
            module.__name__ + '.',
        ):
            submodule = importlib.import_module(module_name)
            add_module(submodule)

    return modules