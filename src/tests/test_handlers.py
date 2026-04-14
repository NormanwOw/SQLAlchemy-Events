import pytest

from sqlalchemy_events import sa_insert_handler


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