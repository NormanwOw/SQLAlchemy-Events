from __future__ import annotations

import sys
import types

import pytest

from sqlalchemy_events import HandlersDiscoveryError, SaEvent
from sqlalchemy_events.discovery import autodiscover
from sqlalchemy_events.registry import HandlerRegistry
from tests.test_models import UserModel


def create_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def create_package(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
    return module


def cleanup_modules(prefix: str) -> None:
    for key in list(sys.modules):
        if key.startswith(prefix):
            del sys.modules[key]


def test_single_module_import() -> None:
    module_name = 'test_module_single'
    create_module(module_name)

    result = autodiscover([module_name])

    assert len(result) == 1
    assert result[0].__name__ == module_name

    cleanup_modules(module_name)


def test_package_with_submodules(monkeypatch) -> None:
    package_name = 'test_package'

    create_package(package_name)
    create_module(f'{package_name}.sub1')
    create_module(f'{package_name}.sub2')

    def fake_walk_packages(path, prefix):
        yield None, f'{package_name}.sub1', False
        yield None, f'{package_name}.sub2', False

    monkeypatch.setattr('pkgutil.walk_packages', fake_walk_packages)

    result = autodiscover([package_name])
    names = {module.__name__ for module in result}

    assert package_name in names
    assert f'{package_name}.sub1' in names
    assert f'{package_name}.sub2' in names

    cleanup_modules(package_name)


def test_multiple_paths(monkeypatch) -> None:
    create_package('pkg1')
    create_package('pkg2')
    create_module('pkg1.mod')
    create_module('pkg2.mod')

    def fake_walk_packages(path, prefix):
        yield None, f'{prefix}mod', False

    monkeypatch.setattr('pkgutil.walk_packages', fake_walk_packages)

    result = autodiscover(['pkg1', 'pkg2'])
    names = {module.__name__ for module in result}

    assert {'pkg1', 'pkg2', 'pkg1.mod', 'pkg2.mod'} <= names

    cleanup_modules('pkg1')
    cleanup_modules('pkg2')


def test_module_without_submodules(monkeypatch) -> None:
    module_name = 'plain_module'
    create_module(module_name)
    monkeypatch.setattr('pkgutil.walk_packages', lambda *args, **kwargs: [])

    result = autodiscover([module_name])

    assert len(result) == 1

    cleanup_modules(module_name)


def test_discovered_handlers_are_registered(monkeypatch) -> None:
    registry = HandlerRegistry()
    monkeypatch.setattr(
        'sqlalchemy_events.handlers.get_event_handlers',
        lambda: registry,
    )

    module_name = 'discovered_handlers_module'
    module = create_module(module_name)
    code = (
        'from sqlalchemy_events import sa_insert_handler\n'
        'from tests.test_models import UserModel\n'
        '@sa_insert_handler(UserModel)\n'
        'async def discovered(rows):\n'
        '    pass\n'
    )
    exec(code, module.__dict__)

    autodiscover([module_name])

    assert registry.get_handlers(None, 'users', SaEvent.INSERT)

    cleanup_modules(module_name)


def test_invalid_autodiscover_path() -> None:
    with pytest.raises(HandlersDiscoveryError, match='not.existing.module') as exc_info:
        autodiscover(['not.existing.module'])

    assert isinstance(exc_info.value.__cause__, ModuleNotFoundError)


def test_invalid_submodule(monkeypatch) -> None:
    package_name = 'test_package_broken'
    create_package(package_name)

    def fake_walk_packages(path, prefix):
        yield None, f'{prefix}missing', False

    monkeypatch.setattr('pkgutil.walk_packages', fake_walk_packages)

    with pytest.raises(
        HandlersDiscoveryError,
        match='test_package_broken.missing',
    ) as exc_info:
        autodiscover([package_name])

    assert isinstance(exc_info.value.__cause__, ModuleNotFoundError)

    cleanup_modules(package_name)


def test_autodiscover_no_duplicates() -> None:
    modules = autodiscover(['tests.handlers', 'tests.handlers.py'])
    names = [module.__name__ for module in modules]

    assert len(names) == len(set(names))


def test_imported_module_is_the_same_object() -> None:
    modules = autodiscover(['tests.test_models'])

    assert modules[0] is sys.modules['tests.test_models']
    assert UserModel.__tablename__ == 'users'
