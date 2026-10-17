"""HTTP 클라이언트의 연결 풀 재사용과 소유권 계약."""

import httpx
import pytest

from completion_bridge.http import AsyncHttpClient, HttpClient


def test_owned_sync_client_is_reused_closed_and_recreated(monkeypatch) -> None:
    raw_type = httpx.Client
    created = []

    def create(**kwargs):
        raw = raw_type(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        created.append(raw)
        return raw

    monkeypatch.setattr(httpx, "Client", create)
    client = HttpClient()
    for _ in range(2):
        assert client.post_json("https://example.test", json={}, headers={}) == {}
    assert len(created) == 1
    client.close()
    assert created[0].is_closed
    client.post_json("https://example.test", json={}, headers={})
    assert len(created) == 2
    client.close()
    assert created[1].is_closed


@pytest.mark.asyncio
async def test_owned_async_client_is_reused_closed_and_recreated(monkeypatch) -> None:
    raw_type = httpx.AsyncClient
    created = []

    def create(**kwargs):
        raw = raw_type(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        created.append(raw)
        return raw

    monkeypatch.setattr(httpx, "AsyncClient", create)
    client = AsyncHttpClient()
    for _ in range(2):
        assert await client.post_json("https://example.test", json={}, headers={}) == {}
    assert len(created) == 1
    await client.aclose()
    assert created[0].is_closed
    await client.post_json("https://example.test", json={}, headers={})
    assert len(created) == 2
    await client.aclose()
    assert created[1].is_closed


def test_injected_sync_client_is_not_closed() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as raw:
        client = HttpClient(raw)
        client.close()
        assert not raw.is_closed
        assert client.post_json("https://example.test", json={}, headers={}) == {}


@pytest.mark.asyncio
async def test_injected_async_client_is_not_closed() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    ) as raw:
        client = AsyncHttpClient(raw)
        await client.aclose()
        assert not raw.is_closed
        assert await client.post_json("https://example.test", json={}, headers={}) == {}
