import sys
import types

from sqlalchemy_events.discovery import autodiscover
from sqlalchemy_events import sa_insert_handler, sa_update_handler, sa_delete_handler
from sqlalchemy_events.registry import get_event_handlers
from tests.test_models import UserModel


def create_module(name: str):
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def create_package(name: str):
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module
    return module


def cleanup_modules(prefix: str):
    for key in list(sys.modules.keys()):
        if key.startswith(prefix):
            del sys.modules[key]


def test_single_module_import():
    module_name = 'test_module_single'
    create_module(module_name)

    result = autodiscover([module_name])

    assert len(result) == 1
    assert result[0].__name__ == module_name

    cleanup_modules(module_name)


def test_package_with_submodules(monkeypatch):
    package_name = 'test_package'

    create_package(package_name)
    create_module(f'{package_name}.sub1')
    create_module(f'{package_name}.sub2')

    def fake_walk_packages(path, prefix):
        yield None, f'{package_name}.sub1', False
        yield None, f'{package_name}.sub2', False

    monkeypatch.setattr('pkgutil.walk_packages', fake_walk_packages)

    result = autodiscover([package_name])

    names = {m.__name__ for m in result}

    assert package_name in names
    assert f'{package_name}.sub1' in names
    assert f'{package_name}.sub2' in names

    cleanup_modules(package_name)


def test_multiple_paths(monkeypatch):
    create_package('pkg1')
    create_package('pkg2')

    create_module('pkg1.mod')
    create_module('pkg2.mod')

    def fake_walk_packages(path, prefix):
        yield None, f'{prefix}mod', False

    monkeypatch.setattr('pkgutil.walk_packages', fake_walk_packages)

    result = autodiscover(['pkg1', 'pkg2'])

    names = {m.__name__ for m in result}

    assert 'pkg1' in names
    assert 'pkg2' in names
    assert 'pkg1.mod' in names
    assert 'pkg2.mod' in names

    cleanup_modules('pkg1')
    cleanup_modules('pkg2')


def test_module_without_submodules(monkeypatch):
    module_name = 'plain_module'
    create_module(module_name)
    monkeypatch.setattr('pkgutil.walk_packages', lambda *args, **kwargs: [])
    result = autodiscover([module_name])
    assert len(result) == 1
    assert result[0].__name__ == module_name

    cleanup_modules(module_name)


@sa_insert_handler(UserModel)
async def insert_handler(): ...


@sa_update_handler(UserModel)
async def update_handler(): ...


@sa_delete_handler(UserModel)
async def delete_handler(): ...


async def test_discover_handlers():
    handlers = get_event_handlers()
    assert handlers
    result_handlers = []
    for key, handler_list in handlers.items():
        assert len(handler_list) == 1
        if 'insert' in key:
            assert handler_list[0].func.__name__ == 'insert_handler'
            result_handlers.extend(handler_list)
        if 'update' in key:
            assert handler_list[0].func.__name__ == 'update_handler'
            result_handlers.extend(handler_list)
        if 'delete' in key:
            assert handler_list[0].func.__name__ == 'delete_handler'
            result_handlers.extend(handler_list)

    assert len(result_handlers) == len(handlers)
