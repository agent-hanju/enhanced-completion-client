"""패키지 재배치 후 공개 이름·하위 모듈·설정 경로의 일관성을 검증한다."""

import completion_bridge as bridge
from completion_bridge.logging import OPERATIONS_LOGGER, WIRE_LOGGER
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


def test_logger_names_are_stable():
    """소비 앱 설정이 참조하는 로거 이름. 원본 통신은 하나, 운영 기록은 하나다."""
    assert WIRE_LOGGER == "completion_bridge.wire"
    assert OPERATIONS_LOGGER == "completion_bridge.operations"
