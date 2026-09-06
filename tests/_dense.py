"""복합 입력 fixture. 오프라인과 라이브 시험이 함께 쓴다.

토큰을 아끼면서 블록 종류를 최대로 싣는 것이 목적이다. 문서와 답변을 짧게 두고 모델에게 긴
생성을 요구하지 않는다. 확인하려는 것은 답변 품질이 아니라 여섯 종류 블록이 각 벤더 wire로
내려가고 서버가 이력을 받아들이는지다.
"""

from __future__ import annotations

from completion_bridge import (
    Citation,
    DocumentBlock,
    HubMessage,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = ["ANSWER", "CITED", "DENSE_TOOL", "dense_history"]

DENSE_TOOL = ToolDefinition(
    name="lookup",
    description="표에서 값을 조회한다",
    input_schema={
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    },
)

ANSWER = "서울은 1000만이다."
CITED = "1000만"
# 인덱스를 손으로 세지 않는다. 문자열에서 찾아 쓰면 문구를 고쳐도 어긋나지 않는다.
def dense_history() -> list[HubMessage]:
    """블록 여섯 종류를 실은 대화 이력.

    - :class:`DocumentBlock` 근거 문서. Anthropic은 네이티브 채널, 나머지는 본문 태그
    - :class:`TextBlock` 사용자 질문과 이전 답변
    - :class:`ThinkingBlock` 이전 턴의 추론. 다른 벤더로는 생략되어야 한다
    - :class:`ToolUseBlock` 이전 턴의 도구 호출
    - :class:`ToolResultBlock` 그 결과. 벤더마다 실리는 자리가 다르다
    - :class:`Citation` 이전 답변 구간의 인용. 어휘가 본문에 태그로 되끼운다
    """
    return [
        HubMessage(
            role="user",
            content=[
                DocumentBlock(id="d1", title="표", text="서울=1000만, 부산=330만"),
                TextBlock(text="서울 인구를 표에서 찾아라."),
            ],
        ),
        HubMessage(
            role="assistant",
            content=[
                ThinkingBlock(thinking="표를 조회해야 한다.", source="prior"),
                ToolUseBlock(id="c1", name="lookup", input_json='{"key":"서울"}'),
            ],
        ),
        HubMessage(role="user", content=[ToolResultBlock(tool_use_id="c1", content="1000만")]),
        HubMessage(
            role="assistant",
            content=[
                TextBlock(text="서울은 "),
                TextBlock(text=CITED, citations=[Citation(source="cite", id="d1")]),
                TextBlock(text="이다."),
            ],
        ),
        HubMessage(role="user", content=[TextBlock(text="부산은? 숫자만.")]),
    ]
