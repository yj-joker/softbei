import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from config.settings import validate_auth_tokens


@pytest.mark.parametrize(
    ("internal_token", "api_token"),
    [
        (None, "api-secret"),
        ("", "api-secret"),
        ("   ", "api-secret"),
    ],
)
def test_validate_auth_tokens_rejects_missing_internal_token(internal_token, api_token):
    with pytest.raises(ValueError, match="internal") as raised:
        validate_auth_tokens(internal_token, api_token)

    assert "api-secret" not in str(raised.value)


@pytest.mark.parametrize("api_token", [None, "", "   "])
def test_validate_auth_tokens_rejects_missing_api_token(api_token):
    with pytest.raises(ValueError, match="API") as raised:
        validate_auth_tokens("internal-secret", api_token)

    assert "internal-secret" not in str(raised.value)


def test_validate_auth_tokens_rejects_equal_tokens_after_trimming():
    with pytest.raises(ValueError, match="相同") as raised:
        validate_auth_tokens(" shared-secret ", "shared-secret")

    assert "shared-secret" not in str(raised.value)


def test_validate_auth_tokens_allows_two_distinct_non_blank_tokens():
    assert validate_auth_tokens(" internal-secret ", "api-secret") is None


def test_lifespan_validates_tokens_before_starting_mq(monkeypatch):
    import api.main as main

    consumer_called = False

    async def fake_start_consumers():
        nonlocal consumer_called
        consumer_called = True

    async def fake_close_connection():
        raise AssertionError("MQ close must not run when startup validation fails")

    consumer_module = types.ModuleType("mq.consumer")
    consumer_module.start_consumers = fake_start_consumers
    connection_module = types.ModuleType("mq.connection")
    connection_module.close_connection = fake_close_connection
    monkeypatch.setitem(sys.modules, "mq.consumer", consumer_module)
    monkeypatch.setitem(sys.modules, "mq.connection", connection_module)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(internal_token="", api_token="api-secret"),
    )

    async def enter_lifespan():
        async with main.lifespan(main.app):
            raise AssertionError("lifespan must not yield with an invalid token contract")

    with pytest.raises(ValueError, match="internal"):
        asyncio.run(enter_lifespan())

    assert consumer_called is False
