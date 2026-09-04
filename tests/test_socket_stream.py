"""실제 소켓 위에서의 스트리밍.

respx는 전송 계층을 가로채므로 SSE 파서가 실제 HTTP chunked 전달을 겪지 않는다. 여기서는
진짜 TCP 서버를 띄우고 SSE 프레임을 일부러 고약한 자리에서 쪼개 보낸다. 청크 경계가 JSON
중간, 개행 사이, ``\\r\\n`` 사이에 떨어지는 경우를 실제 소켓으로 확인한다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx

from enhanced_completion import Bridge, ToolUseBlock
from enhanced_completion.vendors import chat_completions


def _delta(**fields: object) -> str:
    payload = {"id": "c1", "model": "luxia", "choices": [{"index": 0, "delta": fields}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class _SsePieces:
    """SSE 본문을 만들고 임의 크기로 잘라 내놓는다."""

    def __init__(self, body: str, sizes: Sequence[int]) -> None:
        self.body = body
        self.sizes = sizes

    def __iter__(self) -> AsyncIterator[str]:  # pragma: no cover - 타입 편의용
        raise NotImplementedError

    def pieces(self) -> list[str]:
        out: list[str] = []
        rest = self.body
        idx = 0
        while rest:
            size = self.sizes[idx % len(self.sizes)]
            out.append(rest[:size])
            rest = rest[size:]
            idx += 1
        return out


@asynccontextmanager
async def serve(body: str, sizes: Sequence[int]) -> AsyncIterator[str]:
    """SSE를 chunked로 흘리는 최소 HTTP 서버를 띄운다."""
    pieces = _SsePieces(body, sizes).pieces()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # 요청 헤더를 끝까지 읽고 본문은 Content-Length만큼 버린다.
        length = 0
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        if length:
            await reader.readexactly(length)

        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        for piece in pieces:
            raw = piece.encode()
            writer.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
            await writer.drain()
            await asyncio.sleep(0)
        writer.write(b"0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.close()


async def run(body: str, sizes: Sequence[int]) -> tuple[list[str], object]:
    async with serve(body, sizes) as base_url:
        async with httpx.AsyncClient(timeout=10.0) as client:
            bridge = Bridge(
                vendor=chat_completions,
                base_url=base_url,
                model="luxia",
                http_client=client,
            )
            stream = bridge.stream(["안녕"])
            seen = [d.text async for d in stream if d.text]
            return seen, stream.result


class TestRealSocket:
    async def test_whole_frames_in_one_chunk(self) -> None:
        body = _delta(role="assistant") + _delta(content="안녕") + "data: [DONE]\n\n"
        seen, result = await run(body, [len(body)])
        assert seen == ["안녕"]
        assert result.text == "안녕"  # type: ignore[attr-defined]

    async def test_one_byte_at_a_time(self) -> None:
        """가장 고약한 경계. 프레임이 글자 단위로 쪼개진다."""
        body = _delta(content="가") + _delta(content="나") + _delta(content="다")
        seen, result = await run(body, [1])
        assert seen == ["가", "나", "다"]
        assert result.text == "가나다"  # type: ignore[attr-defined]

    async def test_awkward_chunk_sizes_split_json(self) -> None:
        body = _delta(content="hello") + _delta(content=" world") + "data: [DONE]\n\n"
        seen, result = await run(body, [7, 3, 29, 1, 13])
        assert result.text == "hello world"  # type: ignore[attr-defined]
        assert "".join(seen) == "hello world"

    async def test_crlf_line_endings(self) -> None:
        """규격은 CRLF도 허용한다. 청크가 CR과 LF 사이에 떨어질 수 있다."""
        body = (
            _delta(content="a").replace("\n", "\r\n")
            + _delta(content="b").replace("\n", "\r\n")
            + "data: [DONE]\r\n\r\n"
        )
        seen, result = await run(body, [1])
        assert result.text == "ab"  # type: ignore[attr-defined]
        assert seen == ["a", "b"]

    async def test_heartbeat_between_frames(self) -> None:
        body = _delta(content="x") + ": ping\n\n" + _delta(content="y") + "data: [DONE]\n\n"
        seen, result = await run(body, [5])
        assert result.text == "xy"  # type: ignore[attr-defined]
        assert seen == ["x", "y"]

    async def test_no_done_marker_and_no_trailing_blank_line(self) -> None:
        """마지막 프레임을 빈 줄로 닫지 않는 서버가 있다. flush가 그것을 건진다."""
        body = _delta(content="tail").rstrip("\n")
        seen, result = await run(body, [4])
        assert seen == ["tail"]
        assert result.text == "tail"  # type: ignore[attr-defined]

    async def test_tool_calls_across_chunk_boundaries(self) -> None:
        body = (
            _delta(tool_calls=[{"index": 0, "id": "t1", "function": {"name": "get"}}])
            + _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"city":'}}])
            + _delta(tool_calls=[{"index": 0, "function": {"arguments": '"seoul"}'}}])
            + "data: [DONE]\n\n"
        )
        _, result = await run(body, [11, 2, 37])
        block = result.content[0]  # type: ignore[attr-defined]
        assert isinstance(block, ToolUseBlock)
        assert block.id == "t1"
        assert block.name == "get"
        assert json.loads(block.input_json) == {"city": "seoul"}
