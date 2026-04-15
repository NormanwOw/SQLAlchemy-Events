import json
import pytest

from sqlalchemy_events import sa_delete_handler, sa_insert_handler, sa_update_handler
from tests.test_models import UserModel


def test_invalid_model_in_handler():
    with pytest.raises(RuntimeError):
        @sa_insert_handler(object)
        def test_func():
            pass


def test_handler_signature():
    with pytest.raises(TypeError):
        @sa_insert_handler()
        def test_func():
            pass


class Test:
    def test_method(self):
        pass

    @classmethod
    def test_class_method(cls):
        pass

    @staticmethod
    def test_static_method():
        pass


def test_handler_in_class():
    with pytest.raises(RuntimeError):
        sa_insert_handler(UserModel)(Test().test_method)

    with pytest.raises(RuntimeError):
        sa_update_handler(UserModel)(Test.test_class_method)


@sa_insert_handler(UserModel)
async def insert_user_handler(rows):
    assert rows == [1, 2, 3]
    return rows


@sa_update_handler(UserModel)
async def update_user_handler(rows):
    assert rows == [4, 5, 6]
    return rows


@sa_delete_handler(UserModel)
async def delete_user_handler(rows):
    assert rows == [7, 8, 9]
    return rows


@pytest.mark.parametrize(
    'op,rows,resp',
    [
        ('insert', [1, 2, 3], [None, [1, 2, 3]]),
        ('update', [4, 5, 6], [None, [4, 5, 6]]),
        ('delete', [7, 8, 9], [None, [7, 8, 9]]),
    ]
)
async def test_handlers(mock_callback, op, rows, resp):
    response = await mock_callback.handle(
        None, None, None, json.dumps(
            {
                'op': op,
                'table': 'users',
                'rows': rows
            }
        )
    )
    assert response == resp