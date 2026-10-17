# Vocabulary 구현 안내

라이브러리는 블록 등록·필드 병합·태그 파서·변환기 합성을 제공한다. 특정 태그 문법이나
직렬화 템플릿은 강제하지 않는다. 소비 애플리케이션이 Vocabulary 구현을 소유한다.

인용 구현은 [cite_vocabulary.py](../examples/cite_vocabulary.py), 사용자 정의 배지 구현은
[custom_vocabulary.py](../examples/custom_vocabulary.py)에 있다. 둘 다 런타임 패키지의
기본 제공 기능이 아니다. 필요한 파일을 애플리케이션 모듈로 복사하여 수정할 수 있다.
`completion_bridge.CiteVocabulary`, `cite_schema`, `completion_bridge.vocabularies`는 제공하지 않는다.
`Citation`·`TextBlock.citations` 등 공통 도메인 모델과 벤더 네이티브 인용 기능은 유지한다.

## 구현 인터페이스

```python
from collections.abc import Sequence
from typing import Literal

from completion_bridge import ContentBlock, TextBlock, Vocabulary

class MyBlock(ContentBlock):
    type: Literal["my_block"] = "my_block"
    text: str = ""

class MyVocabulary(Vocabulary):
    name = "my-vocabulary"

    def blocks(self) -> tuple[type[ContentBlock], ...]:
        return (MyBlock,)

    def lower(self, blocks: Sequence[ContentBlock]) -> list[ContentBlock] | None:
        # 이 예는 본문을 그대로 사용한다. 태그가 필요하면 속성과 본문을
        # 해당 문법에 맞게 escape하여 직렬화한다.
        if not any(isinstance(block, MyBlock) for block in blocks):
            return None
        result = []
        for block in blocks:
            if isinstance(block, MyBlock):
                result.append(TextBlock(text=block.text))
            else:
                result.append(block)
        return result
```

Vocabulary는 ABC이며 `blocks()`는 필수 일반 추상 메서드다. 고정 튜플을 반환하며,
클래스 속성 또는 프로퍼티로 선언하지 않는다. `lower()`·`lift_mapper()`는 선택 사항이고
기본 구현은 None을 반환한다. 요청용 완성 블록은 lower만 구현해도 된다.
`Bridge(..., vocabularies=[MyVocabulary()])`에 등록하면 Bridge가 블록 타입을 등록한다.

## 응답 변환과 스키마

응답에서 태그를 추출하려면 `lift_mapper()`가 요청마다 독립된 매퍼를 반환해야 한다.
매퍼는 `map(HubResponse) -> list[HubResponse]`와 `flush() -> list[HubResponse]`를 구현한다.
입력 하나에서 출력은 0개·1개·여러 개가 될 수 있다. 입력 델타는 직접 수정하지 않는다.

ContentSchema는 인식할 경로·태그·별칭·속성을 선언한다. 최상위 태그 등록만으로 하위
태그가 자동 등록되지 않는다. 예를 들어 discussion/persona/conclusion은 각각 bind한다.
TagParser의 TextRun·Enter·Exit 이벤트를 블록 델타로 바꾸는 코드는 매퍼가 작성한다.
스키마만으로 필드 매핑·블록 생성·출현 번호 발급이 자동 실행되지는 않는다.
직렬화는 lower에서 직접 구현하며 정해진 템플릿 형식은 없다.

## 병합 선언

StreamMerger는 이미 생성된 델타를 합친다. 태그를 해석하지 않는다. 기본적으로 문자열은
연결하고 숫자는 합산하며, 일반 목록은 추가하고 사전은 최상위 키별로 갱신한다.
중첩 모델과 식별 모델 목록도 처리한다. Literal과 관례적인 index 필드는 교체한다.
생략된 기본값은 최초에만 보존하며 None은 변경 없음이다.

```python
from pydantic import BaseModel, Field
from completion_bridge.streaming.metadata import STREAM_INDEX, STREAM_OVERWRITE

class Speech(BaseModel):
    index: int = Field(json_schema_extra=STREAM_INDEX)
    name: str = Field(default="", json_schema_extra=STREAM_OVERWRITE)
    text: str = ""
```

식별 필드와 속성은 필요한 경우 교체 정책을 선언한다. 사용자 정의 필드 이름이
text가 아니어도 문자열 조각은 연결된다. 정책 키·값·메타데이터는 streaming.metadata에서
가져오며 직접 문자열을 중복 선언하지 않는다. 키는 x-completion-bridge-merge다.
목록 식별값은 정수·정수 튜플처럼 해시 가능해야 한다.

discussion의 conversation을 list[Speech]로 선언하면 Speech.index별로 발언을 누적하고,
conclusion: str도 조각을 연결한다. 같은 persona가 다시 발언할 수 있으므로 이름 대신
persona 태그의 출현 번호를 식별값으로 사용한다. 이 번호는 태그를 처리하는 매퍼가 부여한다.

## seq와 종료

벤더 매퍼는 고정된 최대 길이까지 -1로 정규화한 seq를 발급한다. Vocabulary가 블록을
분할하면 부모 seq 뒤에 0부터 시작하는 조각 번호를 추가한다. 비분할 통과 블록과
AnnotationBlock.target_seq도 동일한 규칙으로 변환한다. seq는 최종 배열 위치가 아니다.
여러 단계의 분할·정규화 규칙은 [Sequence 정책](Sequence-Policy.md)을 따른다.

정상 종료 시 매퍼의 flush가 호출된다. 미완성 태그·종료 태그가 없는 구간을 어떻게
처리할지는 구현에서 결정하고 테스트한다. 앞 단계의 flush 출력도 다음 변환기의 map을
통과한다. StreamMerger.result는 seq를 유지하며, 최종 정렬·index 변환은 finalize_response가 한다.

## 실행과 검증

```text
uv run python examples/cite_vocabulary.py
uv run python examples/custom_vocabulary.py
uv run pytest tests/test_cite.py tests/test_vocabulary.py tests/test_merge.py
```

인용 예제는 로컬 조각을 변환·누적하여 JSON을 출력하며 외부 서버를 호출하지 않는다.
예제 구현은 저장소의 examples 경로에서 테스트하고, 설치 wheel에는 포함하지 않는다.
청크 중간의 태그 분할, 속성 보존, 반복 델타, seq 순서, flush와 요청 직렬화를 검증한다.
common-hitl-chat의 인용 Vocabulary는 이 예제를 import하지 않고 자체 구현을 사용한다.
