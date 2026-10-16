"""패키지 재배치 후 공개 이름·하위 모듈·설정 경로의 일관성을 검증한다."""

import importlib
import json
from pathlib import Path

import completion_bridge as bridge
from completion_bridge.logging import CALLS_LOGGER, LOGGER_NAME, SSE_LOGGER, JsonFormatter
from completion_bridge.models.blocks import TextBlock
from completion_bridge.models.hub import HubMessage
from completion_bridge.models.tools import ToolOutput
from completion_bridge.runtime import RuntimeExecution
from completion_bridge.streaming.mapping import StreamMapper, Vocabulary


def test_public_facade_reexports_original_types():
    """공개 진입점과 책임별 패키지가 같은 타입을 제공한다."""
    assert bridge.TextBlock is TextBlock
    assert bridge.HubMessage is HubMessage
    assert bridge.ToolOutput is ToolOutput
    assert bridge.RuntimeExecution is RuntimeExecution
    assert bridge.StreamMapper is StreamMapper
    assert bridge.Vocabulary is Vocabulary
    for name in bridge.__all__:
        assert getattr(bridge, name) is not None


def test_logging_configuration_resolves_after_module_merge():
    """설정 문자열도 통합한 포맷터를 참조하며 로그 수집 이름은 유지한다."""
    root = Path(__file__).resolve().parent.parent
    config = json.loads(
        (root / "examples/logging.common-hitl-chat.json").read_text(encoding="utf-8")
    )
    qualified_name = config["formatters"]["bridge_json"]["()"]
    module_name, _, class_name = qualified_name.rpartition(".")
    assert getattr(importlib.import_module(module_name), class_name) is JsonFormatter
    assert CALLS_LOGGER == "completion_bridge.wire.calls"
    assert SSE_LOGGER == "completion_bridge.wire.sse"
    assert LOGGER_NAME == "completion_bridge.operations"
