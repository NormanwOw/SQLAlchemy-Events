import importlib
import pkgutil
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from .exceptions import HandlersDiscoveryError


def autodiscover(paths: Iterable[str | Path]) -> list[ModuleType]:
    modules: list[ModuleType] = []
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

        try:
            module = importlib.import_module(path)
        except ModuleNotFoundError as ex:
            raise HandlersDiscoveryError(f'Cannot import handler module {path!r}: {ex}') from ex

        add_module(module)

        if is_file or not hasattr(module, '__path__'):
            continue

        for _, module_name, _ in pkgutil.walk_packages(
            module.__path__,
            module.__name__ + '.',
        ):
            try:
                submodule = importlib.import_module(module_name)
            except ModuleNotFoundError as ex:
                raise HandlersDiscoveryError(
                    f'Cannot import handler module {module_name!r}: {ex}'
                ) from ex

            add_module(submodule)

    return modules
