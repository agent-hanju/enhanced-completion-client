# completion-bridge 0.3.1 변경 보고 — common-mcp-server

- 작성일: 2026-10-02
- 비교 기준: `c833ebd` (`0.3.0`) → 이 문서를 포함하는 `0.3.1` 릴리스 커밋
- 범위: 기준 커밋 이후의 블록 계약, 검색 결과, 벤더 변환, 테스트·문서 변경과 패치 버전 증가
- 패치 버전이지만 **하위 호환성이 깨지는 변경이 포함**된다. 버전 번호만 보고 무수정 교체하면 안 된다.

## 1. 호환성 변경: ContentBlock.source → vendor

모든 `ContentBlock` 계열에서 발급 벤더를 나타내는 필드가 `source`에서 `vendor`로 바뀐다. 생성자, 속성 접근, JSON 저장·복원, 사용자 정의 블록과 vocabulary가 대상이다.

```python
# 0.3.0
TextBlock(text="답변", source="messages")
# 0.3.1
TextBlock(text="답변", vendor="messages")
```

네이티브 데이터 재전송, thinking/signature 처리, 벤더 간 도구 변환과 cite 스트림 식별도 이제 `vendor`를 읽는다. 예전 JSON의 `source`를 자동으로 `vendor`에 매핑하는 호환 alias는 없다. 알려진 블록은 추가 필드를 허용하므로 예전 `source`가 남아도 검증 오류 없이 `vendor=None`이 될 수 있다. 기존 기록을 재사용하는 소비자는 블록의 벤더 식별 필드를 명시적으로 변환해야 한다.

**모든 source 키를 일괄 치환하면 안 된다.** `TextBlock.citations` 내부의 `Citation.source`는 그대로다. 새 `SearchResultBlock.source`는 문서 URL/식별자이며, 벤더 wire payload의 `source`와 서버 검색 도메인의 `source`도 변경 대상이 아니다. 중첩 `ToolResultBlock.blocks`의 이전 블록 기록도 같은 구분이 필요하다.

## 2. SearchResultBlock 추가

공개 export 및 블록 레지스트리에 `SearchResultBlock`이 추가된다. `source`, `title`, `content`는 필수이며, `content`는 비어 있지 않은 문자열 청크 목록이다. `citations_enabled` 기본값은 `True`다.

```python
from completion_bridge import SearchResultBlock, ToolResultBlock

result = ToolResultBlock(
    tool_use_id="call-1",
    content="",
    structured_content={"results": [{"id": "kb://doc-1"}]},
    blocks=[SearchResultBlock(
        source="kb://doc-1",
        title="점검 지침",
        content=["첫 번째 근거 청크", "두 번째 근거 청크"],
    )],
)
```

| 어댑터 | 현재 구현의 검색 결과 전달 방식 |
| --- | --- |
| Messages | 네이티브 `search_result`; 사용자 메시지 최상위 또는 tool result 내부 |
| Chat Completions | `<documents><document id="출처">…</document></documents>` 텍스트 |
| Responses | 같은 문서 텍스트를 `function_call_output`의 `input_text`로 전달 |
| Generate Content | 같은 문서 텍스트를 `functionResponse.response.result`로 전달; 검색 결과를 parts에 중복 삽입하지 않음 |

Messages 어댑터는 요청 전체의 검색 결과 중 하나라도 인용이 켜져 있으면 모든 검색 결과의 인용을 켠다. `DocumentBlock`의 인용 설정과는 별도로 계산한다. 검색 결과 인용이 켜진 요청과 `output_config.format`의 조합은 `MappingError`로 거절한다.

검색 결과를 포함한 Messages tool result는 검색 결과 블록만 허용한다. 같은 결과에 비어 있지 않은 `content`나 `TextBlock`·이미지 등 다른 블록을 섞으면 `MappingError`다. 일반 사용자 메시지 최상위에서 검색 결과와 질문 텍스트를 함께 두는 것은 지원한다.

## 3. 도구 결과와 인용 처리 변경

- `ToolResultBlock.blocks`가 `list[Any]`에서 `list[Block]`으로 바뀐다. 저장 JSON을 복원할 때 중첩 블록도 `type`에 따라 등록된 모델로 복원한다. 알 수 없는 타입은 `VendorBlock`으로 보존한다. 임의 객체를 넣던 호출자는 블록 계약에 맞춰야 한다.
- 검색 결과 블록을 전달하면 모델 입력은 블록에서 만든 근거 본문을 사용하고, 함께 저장한 `structured_content`를 중복 전송하지 않는다. 검색 결과만 있는 Messages tool result의 `content`는 빈 문자열로 둔다.
- 텍스트 변환 경로인 Chat Completions에서는 중첩 블록을 렌더링한 문자열이 있으면 `content` 뒤에 붙이고 `structured_content`를 생략한다. 렌더링 결과가 없으면 `structured_content` JSON, 그마저 없으면 `content`를 사용한다. 따라서 이 필드를 항상 비노출 저장소라고 가정하면 안 된다.
- Messages의 `search_result_location` 인용을 공통 `Citation`으로 변환한다. 출처는 `id`/`uri`, 제목은 `document_title`, 검색 결과 순번은 `document_index`, 청크 범위는 `source_start`/`source_end`에 담는다. 여기서 범위는 검색 결과 `content` 목록의 위치이며 응답 문자열 오프셋이 아니다.
- 해당 인용을 다른 벤더로 재전송하면 출처 식별자를 쓰는 `<cite id="…">` 표현으로 연결한다.
- README, Streaming API, Support Matrix, Conversion Examples, Vendor Tool Conversions, Synthetic Assistant 문서 및 사용자 정의 vocabulary 예제를 새 계약에 맞춘다. 스트림 `seq`/최종 `index` 계약은 이전 `0.3.0`에 이미 포함되었으며 이번 신규 변경이 아니다.

## 4. common-mcp-server 적용 사항

확인한 현재 `common-mcp-server/pyproject.toml`에는 `completion-bridge` 직접 의존성이 없다. `src/mcp_server/server.py`의 공통 MCP 경로는 `result_text()`가 있으면 `ToolResult(content=text, structured_content=result.model_dump(mode="json"))`를 반환한다. 이번 릴리스만으로 서버 결과가 자동으로 `SearchResultBlock`이 되지는 않는다.

적용 지점은 MCP 결과를 `completion_bridge.ToolResultBlock`으로 바꾸는 소비자/채팅 어댑터다. 서버의 MCP `structuredContent`와 라이브러리의 `structured_content`는 경계를 구분하여 보존해야 한다. 검색 결과 스키마를 바꾸려면 서버와 소비자가 출처·제목·청크 목록의 매핑을 별도로 합의해야 한다.

소비자 측 확인 항목:

1. `completion-bridge`를 `0.3.1`로 맞추고 벤더 식별 접근·생성자·저장 JSON을 `vendor`로 전환한다.
2. 검색 결과의 출처 식별자, 제목, 청크를 `SearchResultBlock`으로 구성한다. 서버 도메인의 `source`를 벤더 이름으로 치환하지 않는다.
3. Messages 대상 검색 tool result는 `content=""`, `blocks=[검색 결과만]`으로 만든다. 일반 요약 텍스트를 같은 결과에 덧붙이지 않는다.
4. 도구 결과의 저장·재조회 후 중첩 블록 복원, 모델 입력의 근거 본문과 JSON 중복 여부, 인용 출처와 청크 범위를 확인한다.
5. Messages 및 실제 사용하는 다른 벤더에서 검색→응답→저장→재조회→후속 질문 흐름을 검증한다.

이번 작업은 라이브러리 릴리스와 보고서 전달이다. common-mcp-server의 코드·의존성·기존 작업 파일은 변경하지 않는다.

## 5. 검증

- 오프라인 테스트: `uv run python -m pytest -q` — 467 passed, 27 deselected.
- 정적 검사: Ruff lint 및 mypy 통과. 이번 변경 Python 파일에 Ruff 포맷 적용.
- 새 테스트는 검색 결과 필드 검증, 중첩 JSON 복원, Messages 혼합 결과 거절·인용 설정·인용 변환, 다른 세 어댑터의 근거 텍스트 전달을 검증한다.
- 이번 작업에서 실제 벤더 API 호출 및 common-mcp-server와 연결한 통합 실행은 하지 않았다. 위 결과는 오프라인 테스트와 현재 코드 확인에 한정한다.
