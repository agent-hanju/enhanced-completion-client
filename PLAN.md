# PLAN — LLM 스트리밍 브리지 Python 라이브러리

작성일 2026-09-04. 배경 조사는 [docs/Python-Port-Scope.md](docs/Python-Port-Scope.md)를 따른다.

## 이 문서가 정하는 것

일반 LLM API 서버와 붙는 Python 브리지 라이브러리를 만든다. 소비 프로젝트가 변환용 객체를
등록해 인스턴스를 만들고, 그 인스턴스가 벤더 API와 도메인 모델 사이를 잇는다.

핵심 명제는 하나다. **custom field는 custom content type이고, wire에는 실을 수 없으므로 text
채널로 내리고 올린다.** 네 벤더 API가 모두 text 채널을 가지므로 이 메커니즘은 벤더 독립적이다.

```
요청   도메인 객체 → [내림 lowering] → text 포함 wire 메시지 → 벤더 API
응답   벤더 SSE → [벤더 어댑터] → 허브 델타 → [올림 lifting] → custom block → [병합] → 최종
```

---

# 1. 요구 기능

## 1.1 공개 API

### 1.1.1 Bridge — 진입점

```python
from enhanced_completion import Bridge
from enhanced_completion.vendors import chat_completions

bridge = Bridge(
    vendor=chat_completions,          # 벤더 어댑터 (필수)
    base_url="http://...",            # 엔드포인트 (필수)
    model="luxia3-llm-32b-0901-Q",    # 기본 모델
    api_key=None,
    vocabularies=[CiteVocabulary()],  # 어휘 0..N
    headers={},                       # 사내 게이트웨이용 추가 헤더
    http_client=None,                 # httpx.AsyncClient 주입. None이면 기본 생성
    timeout=120.0,                    # 기본 생성 시에만 적용
)
```

`http_client`를 주입받는 것이 중요하다. 재시도, 커넥션 풀, 프록시, TLS, 타임아웃은 전부 전송
계층의 관심사이고 소비 프로젝트가 이미 정책을 갖고 있다. 브리지가 그것을 다시 정의하지 않는다.

```python
transport = httpx.AsyncHTTPTransport(retries=1)
bridge = Bridge(..., http_client=httpx.AsyncClient(transport=transport))
```

| 메서드 | 시그니처 | 설명 |
|---|---|---|
| `stream` | `(messages, *, tools=None, **params) -> Stream` | 스트리밍. async iterator이자 핸들 |
| `complete` | `(messages, *, tools=None, **params) -> HubResponse` | 끝까지 돌려 병합 결과만 반환 |
| `build_request` | `(messages, ...) -> dict` | 전송할 wire body를 만들어 반환. 디버깅과 통과용 |

`**params`는 `temperature`, `max_tokens` 같은 표준 파라미터와 벤더 확장을 함께 받는다. 어느
것이 확장인지는 벤더 어댑터가 판정한다. 알 수 없는 이름을 조용히 버리지 않고 그대로 실어
보낸다. 서버가 새 필드를 추가해도 라이브러리를 다시 배포하지 않기 위해서다.

### 1.1.2 Stream — 스트리밍 핸들

```python
stream = bridge.stream(messages)

async for delta in stream:            # 델타 소비
    ...

result = stream.result                # 병합된 최종 결과 (완료 후)
await stream.aclose()                 # 취소. HTTP 연결까지 끊는다
```

| 멤버 | 형태 | 설명 |
|---|---|---|
| `__aiter__` / `__anext__` | `AsyncIterator[HubResponse]` | 델타 하나씩 |
| `result` | `HubResponse` | 완료 전 접근 시 `StreamNotFinished` |
| `aclose()` | 코루틴 | 취소. 매퍼 flush 후 부분 결과를 `result`에 남긴다 |
| `raw` | `str \| None` | 원문 누적. 기본 꺼짐 |

**동기 API를 함께 낸다.** `SyncBridge`, `for delta in stream`, `stream.close()`로 같은 모양이다.
파서, 병합기, 어휘, 벤더 어댑터는 전부 순수 함수와 상태 기계라 공유한다. 갈라지는 것은 전송
계층뿐이고 httpx가 `Client`와 `AsyncClient`를 같은 API로 제공한다. 소비 프로젝트가 전부 async는
아니므로 비용 대비 값이 있다.

### 1.1.3 HubResponse — 허브 모델

모든 벤더가 이 타입으로 수렴한다. Anthropic Messages 모양을 따른다. 네 API 중 content가
가장 표현력 있는 유니온이기 때문이다.

```python
class HubResponse(BaseModel):
    id: str | None = None
    model: str | None = None
    role: str | None = None
    content: list[ContentBlock] = []
    stop_reason: str | None = None
    usage: Usage | None = None
```

`content`가 확장점이다. 벤더 정의 블록과 사용자 정의 블록이 같은 리스트에 들어간다.

### 1.1.4 교차 벤더 변환의 범위

다섯 소스를 한 `HubResponse`로 받고, 그것을 `HubMessage`로 바꿔 각 벤더 Request로 다시 쓰는
것이 목표다. 어디까지 참인지 명시한다.

**무손실인 공통 코어.** 텍스트, 이미지, `tool_use`와 `tool_result`, 문서 첨부. 다섯 소스 모두
이 어휘를 갖는다. 이 범위에서는 A로 받아 B로 보내는 것이 온전히 동작한다.

**보존되지만 옮겨지지 않는 것.** 벤더 고유 블록이다. Responses의 `web_search_call`,
`image_generation_call`, `code_interpreter_call`, `mcp_approval_request`. Gemini의
`executableCode`, `codeExecutionResult`, `inlineData`, safety rating, citation metadata.
agent SSE의 `activity`, `sources`, `skill_run`, `memory_write`.

이들은 각각 블록 타입으로 등록하고 **원본 페이로드를 그대로 들고 있는다.** 그러면 A → 허브 →
A 왕복은 무손실이고, A → 허브 → B는 `lower`가 text로 내리거나 드롭한다. 어느 쪽인지는 블록이
정한다. 개방형 유니온을 쓰는 이유가 여기다.

**의미상 옮길 수 없는 것.** 이건 구조가 아니라 계약 문제다.

| 대상 | 제약 |
|---|---|
| Anthropic `thinking` | signature가 붙고 원문 그대로 되돌려야 한다. 다른 벤더로 못 보낸다 |
| Responses `reasoning` | 암호화된 내용이고 발급한 응답에 묶인다 |
| tool call ID | 형식이 벤더마다 다르고 대화 안에서 짝이 맞아야 한다 |
| 캐시 지시자 | `cache_control` 등은 벤더 전용 |

추론 블록은 **원 벤더로 되돌릴 때만** 안전하다. 허브 모델이 블록의 출처(`source` 태그)를 들고,
다른 벤더로 내릴 때 자동으로 떨어뜨린다.

**agent SSE는 요청 방향이 축약된다.** `execute`가 `message` 문자열 하나만 받고 messages 배열을
받지 않는다. 이력은 서버가 `sessionId`로 관리한다. 응답 → 허브는 완전하지만 허브 → 요청은
마지막 사용자 메시지를 평탄화하는 것이 전부다. 실질적으로 읽기 전용 스포크다.

## 1.2 확장 사용법

### 1.2.1 Content block 추가

기본 제공 블록은 `text`, `thinking`, `tool_use`, `tool_result`, `image`다. 새 블록은
`ContentBlock`을 상속하고 `type`을 Literal로 고정한다.

```python
from typing import Literal
from enhanced_completion import ContentBlock

class CitationBlock(ContentBlock):
    type: Literal["citation"] = "citation"
    id: str
    text: str = ""
    start_index: int = 0
    end_index: int = 0
```

병합 규칙은 필드 메타로 선언한다. 기본은 이어붙이기이고 예외만 표시한다.

```python
class CitationBlock(ContentBlock):
    type: Literal["citation"] = "citation"
    id: str = Field(default="", json_schema_extra={"stream": "overwrite"})
    text: str = ""                                    # 이어붙이기 (기본)
    index: int | None = None                          # 리스트 짝짓기 키
```

### 1.2.2 Vocabulary — 어휘 등록

블록 하나와 그 블록의 내림·올림 규칙을 한 객체로 묶는다. 요청 직렬화와 응답 파싱이 어긋나지
않게 하는 것이 목적이다. Java에서는 이 둘이 다른 패키지에 흩어져 하드코딩으로만 일치했다.

```python
from enhanced_completion import Vocabulary, ContentSchema, ParseEvent

class CiteVocabulary(Vocabulary):
    blocks = [CitationBlock]

    def schema(self) -> ContentSchema:
        """응답: 인식할 태그. 경로와 태그 이름을 분리해 선언한다."""
        return (ContentSchema()
                .bind("/cite").tag("cite").alias("rag").attr("id")
                .bind("/cite/id").tag("id"))

    def lift(self, ev: ParseEvent, st: LiftState) -> Iterable[ContentBlock]:
        """올림: 태그 파서 이벤트를 블록으로."""
        match ev:
            case Enter("/cite", attrs):
                st.open(CitationBlock(id=attrs.get("id", ""),
                                      start_index=st.cursor))
            case Text("/cite", s):
                yield CitationBlock(text=s)
            case Exit("/cite"):
                yield st.close(end_index=st.cursor)
            case Text("/cite/id", s):
                st.buffer(s)                # 본문 인덱스에 반영하지 않는다

    def lower(self, block: CitationBlock) -> str:
        """내림: 블록을 요청 text로 되쓴다."""
        return f'<cite id="{block.id}">{block.text}</cite>'
```

`lower`가 없으면 그 블록은 요청에 실리지 않고 조용히 버려진다. 대화를 이어갈 때 이전 답변이
요청에 되실리므로 왕복이 실제로 일어난다. `lift`와 `lower`의 태그 어휘가 같아야 한다.

### 1.2.3 Vendor adapter — 벤더 추가

```python
from enhanced_completion import VendorAdapter, StreamMapper, SseFrame

class MyVendor(VendorAdapter):
    name = "my-vendor"
    path = "/v1/chat/completions"

    def build_body(self, req: HubRequest) -> dict:
        """허브 요청을 이 벤더 wire body로."""

    def decode(self, frame: SseFrame) -> object | None:
        """SSE 프레임 하나를 벤더 chunk 객체로. None이면 무시."""

    def is_terminal(self, frame: SseFrame) -> bool:
        """종료 판정. OpenAI는 [DONE], 이름 붙은 이벤트를 쓰는 서버는 이벤트명으로."""

    def to_hub(self) -> StreamMapper:
        """벤더 chunk 스트림을 허브 델타 스트림으로. 상태를 갖는 새 인스턴스."""
```

`decode`와 `is_terminal`이 프레임 전체를 받는 것이 핵심이다. `data` 필드만 넘기면 이름 붙은
이벤트를 쓰는 서버에 대응할 수 없다.

기본 제공 어댑터는 `chat_completions` 하나로 시작하고 `messages`, `responses`,
`generate_content`를 순차 추가한다.

### 1.2.4 IMessageable — 요청 직렬화

도메인 객체를 그대로 `messages`에 넣을 수 있게 한다. 반환이 리스트인 것이 중요하다. tool call과
그 결과를 한 객체로 들고 있다가 전송 시 둘로 펼칠 수 있다.

```python
from enhanced_completion import IMessageable, HubMessage

class ChatTurn(IMessageable):
    def to_messages(self) -> list[HubMessage]:
        return [HubMessage.user(self.question),
                HubMessage.assistant(blocks=self.answer_blocks)]
```

`HubMessage`도 `list[ContentBlock]`을 들므로 `CitationBlock`을 그대로 넣으면 `lower`가
text로 내려준다.

### 1.2.5 StreamMapper 합성

파이프라인 단계는 전부 `StreamMapper`이고 합성이 닫혀 있다.

```python
class StreamMapper(Protocol[T, R]):
    def map(self, delta: T) -> list[R]: ...
    def flush(self) -> list[R]: ...

def compose(f: StreamMapper[A, B], g: StreamMapper[B, C]) -> StreamMapper[A, C]: ...
```

합성의 `flush` 순서가 계약이다.

```
map(a)  = [c for b in f.map(a)  for c in g.map(b)]
flush() = [c for b in f.flush() for c in g.map(b)] + g.flush()
```

`f`의 잔여 버퍼가 `g`를 거친 다음에 `g.flush()`가 와야 한다. 스트림 끝에서 닫히지 않은 태그가
정확히 이 경로를 탄다. 순진하게 `f.flush() + g.flush()`로 쓰면 `f`의 마지막 출력이 `g`를
건너뛴다.

상태를 가진 매퍼는 파이프라인마다 새 인스턴스여야 한다. 그래서 `to_hub()`가 인스턴스가 아니라
팩토리다.

## 1.3 비기능 요구

| 항목 | 요구 |
|---|---|
| 취소 | `aclose()`가 HTTP 연결까지 끊고 부분 결과를 남긴다 |
| 재시도 | **라이브러리가 갖지 않는다.** 전송 계층과 소비 앱의 몫 |
| 재연결 | 하지 않는다. chat completions 스트림은 재개 수단이 없다 |
| 메모리 | 원문 누적 기본 꺼짐. 긴 스트림에서 입력 길이에 비례해 늘지 않는다 |
| 비밀정보 | 로그와 예외 메시지에 프롬프트, 응답 원문, API key를 넣지 않는다 |
| 설정 | 엔드포인트와 모델명을 코드에 박지 않는다. 전부 생성자 인자 |

### 재시도를 넣지 않는 이유

이 라이브러리는 `common-hitl-chat` 같은 소비 앱에서 provider 자리에 놓인다. 그 자리는 재시도
정책을 소유하지 않는다. 실제로 `common-hitl-chat`이 그렇게 하고 있다.

| 층 | 무엇을 갖는가 | 근거 |
|---|---|---|
| 소비 앱 설정 | 정책(횟수) | `application.yml`의 `max-retries: ${HITL_CHAT_LLM_MAX_RETRIES:1}` |
| provider | 전달만 | `OpenAiCompatibleLlmProvider`가 `.maxRetries(maxRetries)`로 넘긴다 |
| HTTP 클라이언트 | 메커니즘 | OkHttp / OpenAI SDK |

CYCLE-13의 사고는 이 구조를 확인해 준다. MCP Tool 실행 뒤 후속 모델 호출이 재사용 HTTP 연결의
EOF로 실패했고, 재시도를 0회로 강제하던 **설정값**을 1회로 바꿔 복구했다. 고친 것은 provider
코드가 아니라 설정이다.

`httpx`에는 그 경계가 이미 원시로 있다. `AsyncHTTPTransport(retries=N)`은 httpcore의 커넥션 풀로
전달되어 **연결 수립 실패만** 재시도한다. 헤더가 도착한 뒤에는 요청 재전송도, 본문 스트림 재시도도
하지 않는다. 스트리밍 POST를 응답 시작 후에 재시도하면 서버가 이미 처리한 요청을 다시 보내게
되므로 안전하지 않은데, httpx가 그 선을 이미 긋고 있다.

따라서 브리지는 `http_client` 주입만 받는다. 소비 앱이 transport에 자기 정책을 걸면 된다.
편의를 위해 `max_retries` 같은 통과 인자를 두지 않는다. 하나를 두면 타임아웃, 프록시, 커넥션
한도, TLS가 차례로 따라 들어오고 결국 httpx 설정을 두 벌로 관리하게 된다.

참고로 현행 Java 클라이언트는 `WebClient.Builder`를 받으므로 이미 같은 방식이다. 호출자가
커넥터를 구성한다.

---

# 2. 레거시 자산

## 2.1 이식 대상과 참조 대상

| 자산 | 위치 | 상태 | 처리 |
|---|---|---|---|
| `streambind` | JitPack 0.1.0 / 0.1.1 | 로컬 소스 없음, jar만 | **재작성.** 병합 규칙 표를 계약으로 |
| `streambind-base` | `~/workspace/streambind-base` main + `origin/develop` | 4벤더 DTO 4,404줄 | **스펙 참조 + 부분 이식.** compatible 우선 |
| `content-stream-adapter` | `~/workspace/content-stream-adapter` `refactor` v0.3.0 | 신 API | **재작성.** 신 API 기준 |
| `fluxhandle` | JitPack 0.4.2 | Reactor 종속 | **이식 안 함.** async iterator로 대체 |
| `enhanced-completion-client` | 이 저장소 | cite 어휘 구현 + 테스트 1,027줄 | **테스트를 인수 조건으로 이식** |
| `common-hitl-chat` | `align-dev-hanju-tool-stream` 브랜치 | 사내 SSE 계약 문서 | **스펙 참조** |
| `common-mcp-server` | `~/workspace/common-mcp-server` | Python + uv 관례 | **설정 복사** |

## 2.2 자산별 상세

### streambind — 병합 엔진 (재작성)

계약이 되는 병합 규칙은 다음과 같다. 기본이 이어붙이기이고 예외만 표시한다는 점이 핵심이다.

| 필드 종류 | 동작 | Java 표시 | Python 표시 |
|---|---|---|---|
| String | 이어붙이기 | (기본) | (기본) |
| Number | 더하기 | (기본) | (기본) |
| 객체 | 재귀 병합 | (기본) | (기본) |
| primitive List | 뒤에 추가 | (기본) | (기본) |
| 객체 List | index 짝짓기 | `@StreamIndex` 또는 이름이 `index` | `{"stream": "index"}` |
| 무엇이든 | 덮어쓰기 | `@StreamOverwrite` | `{"stream": "overwrite"}` |

Java 1,362줄 중 `TypeVariableResolver`가 큰 몫을 차지하는데 이것은 제네릭 소거 대응이다.
Python은 `model_fields`가 실제 타입을 들고 있어 그 층이 통째로 사라진다. Map과 배열, record
지원도 뺀다. 300~400줄로 본다.

**주의.** streambind 0.1.1은 로컬 캐시에 없어 확인하지 못했다. 0.1.0의 `StreamMapper`에는
합성 연산자가 없고 `map`, `flush`, `apply(Flux)`뿐이다. 합성은 새로 만든다.

### streambind-base — 벤더 페이로드 (스펙 참조)

HTTP 클라이언트가 없는 순수 DTO와 매퍼 레이어다. 줄 수는 다음과 같다.

| 프로바이더 | 줄 수 | 요청 seam | 응답 매퍼 |
|---|---|---|---|
| Anthropic Messages | 1,346 | `IAnthropicable.toAnthropicMessage()` | `MessagesResponseMapper` |
| OpenAI Chat Completions | 496 | `IMessageable.toMessages()` | — |
| OpenAI Responses | 1,822 | `IResponsesable.toOpenAiItems()` | `ResponsesResponseMapper` |
| Gemini GenerateContent | 740 | `IGeminiable.toGeminiMessage()` | `GenerateContentResponseMapper` |

**`origin/develop`에 허브 수렴 설계가 이미 있다.** `TODO.md` 2번 항목이 그 계획이고,
`ChatCompletionsToMessagesMapper`, `GeminiToMessagesMapper`, `ResponsesToMessagesMapper` 세
개가 구현돼 있다. 전부 `StreamMapper<VendorResponse, MessagesResponse>`다. 즉 스트림 레벨
허브 앤 스포크가 Java에서 이미 검증됐다. Python 설계는 이 구조를 그대로 따른다.

변환 테스트 716줄(`MessagesResponseConversionTest` 520, `MessageConversionTest` 196)이
허브 매핑의 인수 조건이다.

**이식하지 않고 참조만 하는 이유.** 4,404줄 대부분이 Lombok 보일러플레이트다. Pydantic으로
옮기면 필드 하나가 한 줄이 되므로 다시 쓰는 편이 빠르다. 필드 목록과 별칭, 매핑 규칙만 가져온다.

**단, 확장을 막는 지점이 하나 있다.** `ContentBlock`, `ToolUseBlock`, `ToolResultBlock`,
`Item`, `ContentPart`, `Annotation`이 전부 `sealed`이고 리프가 `final`이다. 사용자가 블록을
추가할 수 없다. Java에서 `switch` 패턴 매칭의 완전성 검사를 얻는 대가였다. Python에는 그 검사가
없으므로 유니온을 런타임 개방으로 만든다.

### content-stream-adapter — 태그 파서 (재작성)

`refactor` 브랜치의 v0.3.0을 기준으로 한다. `main`이 아니다. 0.1.6 대비 바뀐 점은 다음과 같다.

- 경로와 태그 이름 분리. `TransitionSchema.path()`로 의미 경로를, `bind().tag().alias().attr()`로 표면 문법을 건다
- 출력이 문자열 이벤트에서 `Text` / `Enter(path, attrs)` / `Exit(path)` 세 타입으로
- 태그 이름 경계 정확화. 0.1.6은 `<cite` 접두만 봐서 `<citation>`도 걸렸다
- `captureRaw(false)`로 원문 누적을 끌 수 있다
- 같은 태그 이름이 같은 부모 아래 형제 경로에 바인딩되면 빌드 시점 거부
- 빈 델타 허용. 빈 문자열은 빈 리스트, `None`은 예외

Aho-Corasick은 옮기지 않는다. 실제 패턴 수가 태그당 여섯 개 미만이라 단순 트라이로 충분하다.
250~350줄로 본다.

**이식의 인수 조건.** 이 저장소의 `EnhancedCompletionDeltaMapperTest` 1,027줄이 계약을
고정한다. 한 글자씩 입력, 태그가 청크 경계에서 쪼개지는 경우, 닫는 태그 끝과 여는 태그 시작이
한 청크에 들어오는 경우, 빈 인용, 미닫힘 태그를 모두 검증한다. 전부 이식한다.

### enhanced-completion-client — cite 어휘 (이식)

`EnhancedCompletionDeltaMapper`의 상태 기계를 `CiteVocabulary`로 옮긴다. 상태 네 개는 그대로다.
본문 문자 커서, 인용 시작 인덱스, 인용 ID 버퍼, 인용 일련번호.

**인용 태그 문법이 두 갈래인 점을 확정해야 한다.** 이 저장소는 `<cite><id>X</id>본문</cite>`
(중첩 태그), `common-hitl-chat`의 `CitationAwareLlmProvider`는 `<cite id="X">본문</cite>`
(속성)이다. 사내 모델의 실제 출력이 어느 쪽인지 0단계에서 확인한다. `ContentSchema`가 둘 다
표현할 수 있으므로 스키마 선언만 달라진다.

이식하지 않는 것은 augment(RAG) 일체다. `Augmenter`, `AugmentResult`, `SimpleAugmentResult`,
`AugmentResultDeltaMapper`와 클라이언트의 RAG 분기가 대상이다. 문서 첨부(`AttachedMessage`,
`IDocument`)는 augment와 독립이므로 어휘 하나로 옮긴다.

### agent-studio 1.4.1 — 사내 agent SSE (확정)

`/d/반입용/agent-studio-v1.4.1.tar`의 프론트 이미지에서 확인했다. 별도 메모는 필요 없다. 소비
코드가 곧 스펙이다.

- 이미지: `saltlux-agent-studio-front-1.4.1.tar` (68 MB)
- 레이어: `usr/share/nginx/html/assets/`
- 파일: `sse-DmI5tDR-.js`, `SingleAgentService-*.js`, `SingleAgentChat-*.js`, `CodeAgentChat-*.js`

**엔드포인트.** `POST /console/api/single-agents/{id}/execute`, `POST /console/api/code-agents/{id}/execute`.
`Accept: text/event-stream`이고 `EventSource`가 아니라 `fetch`를 쓴다. POST이기 때문이다.

```json
{ "message": "...", "sessionId": "...", "fileIds": [], "attachmentIds": [],
  "webSearchEnabled": false, "responseMode": "streaming", "taskId": "..." }
```

인증은 `csrf_token` 또는 `__Host-csrf_token` 쿠키를 `X-CSRF-Token` 헤더로 보내고 `Authorization`을
함께 싣는다. 에이전트를 API로 공개하는 경로도 있다(`/api-enable`, `/api-keys`).

취소는 `POST /{kind}-agents/{id}/tasks/{taskId}/stop`이다. 연결 종료가 취소가 아니다.
`common-hitl-chat`과 같은 규칙이다.

**프레임 파싱 규칙.** `sse-DmI5tDR-.js`가 그대로 계약이다.

| 규칙 | 내용 |
|---|---|
| `data` 접두 | `data: `와 `data:` 둘 다 허용. 공백이 없을 수 있다 |
| 종료 | `[DONE]` 페이로드와 `done` 이벤트 둘 다 |
| 이벤트 이름 | SSE `event:` 필드. 없거나 `message`면 **본문 JSON의 `event` 또는 `type`** 을 본다 |
| 본문 | JSON 파싱 실패 시 원문 문자열로 폴백. JSON이 아닌 프레임이 있다 |

**본문에서 텍스트를 꺼내는 경로가 아홉 개다.** 클라이언트가 순서대로 훑는다.

```
content → body → answer → delta → text → message.content
→ choices[0].delta.content → choices[0].message.content → data
```

이것이 "사내 특이 SSE 동작"의 실체다. 필드 이름이 고정돼 있지 않고, OpenAI 모양
(`choices[0].delta.content`)과 자체 모양이 한 스트림에 섞인다.

**이벤트 어휘.** single agent와 code agent가 공통 골격을 쓰고 code agent가 도구 이벤트를 더 갖는다.

| 분류 | 이벤트 |
|---|---|
| 수명주기 | `run_start`, `done`, `error`, `failed`, `session`, `task`, `title` |
| 본문 | `agent_message`, `message`, `message_delta`, `answer`, `chunk`, `delta`, `text_chunk` |
| 추론 | `reasoning_delta` |
| 도구 | `tool_call`, `tool_result` |
| 진행 | `activity`, `activity_done` |
| 인용 | `sources` |
| code agent 전용 | `bash`, `edit`, `read`, `write`, `glob`, `grep`, `web_search`, `web_fetch`, `memory_read`, `memory_write`, `skill_read`, `skill_run`, `agent`, `agent_wait` |

본문 이벤트에 별칭이 일곱 개다. 어댑터가 이것을 하나로 정규화한다. code agent의 도구 이벤트는
Claude Code 계열 도구 이름과 같으므로 `tool_use` 블록으로 올린다.

`sources`는 인용이다. cite 어휘와 같은 자리로 모을 수 있는지 실제 페이로드를 보고 정한다.

**이 스포크는 요청 방향이 축약된다.** `execute` 본문이 `message` 문자열 하나이고 messages 배열을
받지 않는다. 대화 이력은 서버가 `sessionId`로 관리한다. 따라서 허브 → agent Request는 마지막
사용자 메시지를 문자열로 평탄화하는 것이 전부다. 벤더 축에서 사실상 읽기 전용 스포크다.

### common-hitl-chat — 서버측 SSE 스펙 (참조)

브랜치 `align-dev-hanju-tool-stream`의 `docs/API_SPECIFICATION.md`에만 있다. main에는 없다.
표준 OpenAI 스트림과 다른 점은 다음과 같다.

- 이름 붙은 이벤트. turn 상태 4종, HITL 8종, tool stream 6종, 스냅샷 1종
- 종료 표지가 없다. terminal 이벤트 이름으로 판별
- `eventId`가 단조 증가 replay 커서. `Last-Event-ID`로 재연결
- 커서가 버퍼(1,024건) 밖이면 스냅샷 이벤트 후 실시간 접속
- 15초 주석 전용 하트비트
- 클라이언트 연결 해제가 취소가 아니다

같은 저장소의 `LlmDialect`는 벤더 확장 필드를 분리하는 seam이다. vLLM 전용
`chat_template_kwargs`를 OpenAI에 보내다 400을 받은 기록이 주석에 있다. `VendorAdapter`의
`build_body`가 같은 역할을 한다.

`CitationAwareLlmProvider`는 content-stream-adapter 신 API 사용 예시다.

### common-mcp-server — Python 관례 (복사)

`requires-python = ">=3.12"`, `uv_build` 백엔드, src 레이아웃, ruff line-length 100,
mypy strict, pytest-asyncio auto 모드. 이 프로젝트도 같게 맞춘다.

---

# 3. 스택

## 3.1 런타임과 빌드

| 항목 | 선택 | 근거 |
|---|---|---|
| Python | `>=3.12` | `common-mcp-server`와 동일. `match` 문과 `X \| Y` 문법 사용 |
| 패키지 관리 | uv | 사내 Python 프로젝트 관례 |
| 빌드 백엔드 | `uv_build` | `common-mcp-server`가 이미 사용 중 |
| 레이아웃 | src | `uv_build`의 `module-root` 기본값 |

```console
uv init --lib enhanced-completion-client
uv build          # dist/에 wheel과 sdist
```

`pyproject.toml` 초안이다.

```toml
[project]
name = "enhanced-completion-client"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.9",
]

[project.optional-dependencies]
sync = []                       # 동기 API는 httpx만으로 충분

[build-system]
requires = ["uv_build>=0.11.32,<0.12.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "enhanced_completion"

[dependency-groups]
dev = ["ruff>=0.7", "mypy>=1.13", "pytest>=8.3",
       "pytest-asyncio>=0.24", "pytest-cov>=6.0", "respx>=0.21"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.mypy]
python_version = "3.12"
strict = true
files = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["live: requires a real endpoint"]
```

배포 이름은 Java 저장소와 같은 `enhanced-completion-client`, 모듈 이름은 `enhanced_completion`으로
확정했다. PyPI 공개 배포를 할지 사내 인덱스나 git 의존으로만 쓸지는 아직 미결이다. 공개한다면
이름 선점 여부를 먼저 확인한다.

## 3.2 런타임 의존

| 라이브러리 | 용도 | 대안과 판단 |
|---|---|---|
| `httpx` | HTTP 전송. 동기·비동기 동일 API | `aiohttp`는 동기가 없다. httpx가 맞다 |
| `pydantic` v2 | 모델, 별칭, 판별 유니온 | 필드 메타로 병합 규칙까지 실을 수 있다 |

**의존은 이 둘로 끝낸다.** 나머지는 직접 만든다.

### SSE 파서 — 직접 구현

`httpx-sse`가 `event`, `data`, `id`, `retry`를 모두 노출하므로 후보였다. 그럼에도 직접 만드는
이유는 둘이다.

- 종료 판정이 벤더마다 다르다. `[DONE]`이 없는 서버가 있어 프레임 전체를 어댑터에 넘겨야 한다
- 파서 자체가 60줄 남짓이고, 의존을 둘로 유지하는 값이 그보다 크다

`httpx-sse`는 참조 구현으로 본다. 위 두 이유가 약해지면 갈아타도 된다.

**재연결은 이 라이브러리의 요구가 아니다.** OpenAI 호환 `/v1/chat/completions` 스트림에는 `id:`
필드가 없어 재개할 수 없다. 끊기면 잃는다. `Last-Event-ID` replay와 스냅샷 폴백은
`common-hitl-chat`이 **브라우저에게** 내보내는 Turn 이벤트 스트림의 성질이고
(`TurnController`의 `@RequestHeader("Last-Event-ID")`), LLM 엔드포인트와는 무관하다. 그 스트림도
소비하기로 정하면 그때 어댑터와 함께 재연결 정책을 넣는다. 5절 미결 사항이다.

참고로 현행 Java 클라이언트에는 재시도도 재연결도 없다.

### 태그 파서, 병합기, 합성 — 직접 구현

대응하는 라이브러리가 없다. 2.2절 참조.

## 3.3 공식 벤더 SDK

| SDK | 스트리밍 경로 사용 | 용도 |
|---|---|---|
| `openai` | 사용하지 않음 | 요청 스키마 참조 |
| `anthropic` | 사용하지 않음 | 허브 모델 스키마 참조 |
| `google-genai` | 사용하지 않음 | 요청 스키마 참조 |

**스트리밍 경로에서 벤더 SDK를 쓰지 않는다.** 이유가 셋이다.

- SDK가 SSE 프레임 층을 감춘다. 이름 붙은 이벤트와 `id`를 못 본다. 사내 SSE에 대응할 수 없다
- SDK의 누적기는 자기 타입만 안다. 우리 custom block을 모른다
- SDK가 응답을 자기 닫힌 타입으로 정규화한다. 확장 유니온과 맞지 않는다

다만 **요청 body를 외부에서 받는 통로는 연다.** 이미 벤더 SDK로 요청을 만들고 있는 소비
프로젝트가 그것을 그대로 넘길 수 있게 한다.

```python
bridge.stream(body=sdk_built_dict)     # messages 대신 완성된 body를 직접
```

`common-mcp-server`가 `openai>=3.0.0`을 이미 의존하므로 이 통로가 실제로 쓰인다.

## 3.4 런타임 개방형 유니온 — 직접 구현

Pydantic이 제공하는 판별 유니온은 두 가지다.

- `Field(discriminator="type")` — 애노테이션에 유니온 멤버가 고정된다
- `Annotated[X, Tag("x")] | ..., Discriminator(callable)` — 콜러블로 태그를 뽑되 멤버는 여전히 고정

**둘 다 정의 시점에 닫힌다.** 런타임에 블록 타입을 추가하려면 레지스트리를 직접 붙인다.

```python
_REGISTRY: dict[str, type[ContentBlock]] = {}

def register_block(cls: type[ContentBlock]) -> None:
    _REGISTRY[cls.model_fields["type"].default] = cls

def _dispatch(v: Any) -> ContentBlock:
    if isinstance(v, ContentBlock):
        return v
    cls = _REGISTRY.get(v.get("type"), UnknownBlock)
    return cls.model_validate(v)

Block = Annotated[ContentBlock, BeforeValidator(_dispatch)]
```

`UnknownBlock`으로 폴백하는 것이 중요하다. 벤더가 새 블록 타입을 추가해도 스트림 전체가
깨지지 않는다. Java의 `@JsonIgnoreProperties(ignoreUnknown = true)`와 같은 역할이다.

`Vocabulary`를 `Bridge`에 등록하면 그 `blocks`가 자동으로 레지스트리에 들어간다. 사용자가
`register_block`을 직접 부르지 않아도 된다.

## 3.5 개발 도구

`common-mcp-server`와 동일하게 맞춘다. ruff(line-length 100, `E,F,I,UP,B`), mypy strict,
pytest + pytest-asyncio(auto) + pytest-cov. HTTP 목킹은 `respx`를 쓴다. httpx 전용이라
전송 계층을 그대로 두고 SSE 프레임을 주입할 수 있다.

라이브 시험은 `live` 마커로 분리하고 환경변수가 있을 때만 돈다. 엔드포인트와 모델명을 코드에
넣지 않는다. 지금 Java E2E 시험에 사내 주소와 모델명이 박혀 있는데 반복하지 않는다.

---

# 4. 착수 순서

## 0단계 — 실제 응답 원문 확보 (완료)

로컬 `vllm-qwen3-8b` 컨테이너(`vllm/vllm-openai:v0.28.0-cu129`, `127.0.0.1:8000`)를 대상으로
확인했다. 사내 LUXIA 서버(`172.16.100.200:14100`)는 이 시점에 닿지 않았고, `luxia-serving`의
설정은 120B에 GPU 2장을 요구해 이 장비에서는 띄울 수 없다.

| 항목 | 확인 결과 |
|---|---|
| 추론 필드 이름 | **`reasoning`**. `reasoning_content`가 아니다 |
| 종료 표지 | `data: [DONE]` 있음 |
| 이름 붙은 이벤트 | 없음. `data:`만 쓴다 |
| `data:` 공백 | 항상 `data: ` (공백 있음) |
| 선언 밖 필드 | `prompt_token_ids`, `prompt_text`, `token_ids`, `system_fingerprint`가 섞여 온다 |
| tool call | `index`로 조각이 오고 `id`·`name`은 첫 조각에만 온다 |
| `usage` | 기본으로 오지 않는다. `stream_options`로 요청해야 한다 |

`common-hitl-chat`의 API 명세가 "reasoning parser가 분리한 최신 vLLM의 `reasoning`만
reasoning event로 취급하며 `reasoning_content`와 inline `<think>` fallback은 지원하지 않는다"고
적어둔 것과 일치한다. 어댑터는 둘 다 보지만 실제로 오는 것은 앞쪽이다.

선언 밖 필드가 섞여 오는 것이 설계 판단을 뒷받침한다. 허브 모델이 `extra="allow"`이고 어댑터가
아는 필드만 읽으므로 무해하게 흘러간다. 엄격한 모델을 썼다면 첫 프레임에서 터졌을 자리다.

**thinking 모델은 예산을 추론이 먼저 쓴다.** `max_tokens`를 작게 주면 `content`가 비고
`stop_reason`이 `length`가 된다. 짧은 답을 받으려면 vLLM 전용 확장
`chat_template_kwargs={"enable_thinking": false}`가 필요하다. 같은 필드를 OpenAI에 보내면 요청이
통째로 400으로 거절되므로, 벤더가 둘 이상이 되면 `LlmDialect`에 해당하는 판단을 어댑터가
가져야 한다.

**인용 태그 문법은 모델이 정하지 않는다.** 프롬프트에 어떤 태그를 쓰라고 지시하지 않으면 모델이
입력 문서의 태그를 그대로 흉내낸다. 실측에서 `대한민국의 수도는 <document id="d1">서울</document>
이다.`가 나왔다. 즉 이 문법은 우리가 고르는 것이고, 어휘가 프롬프트 지시와 파서 스키마를 함께
들어야 한다. `Vocabulary`가 두 방향을 한 객체에 묶는 이유가 하나 더 늘었다.

### 재현

```bash
curl -sN -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-8b","messages":[{"role":"user","content":"안녕"}],
       "stream":true,"max_tokens":8}' \
  http://127.0.0.1:8000/v1/chat/completions
```

라이브 시험은 환경변수로만 돈다. 주소와 모델명을 코드에 넣지 않는다.

```bash
ECC_LIVE_BASE_URL=http://127.0.0.1:8000 ECC_LIVE_MODEL=qwen3-8b \
  uv run pytest -m live -s
```

## 0단계 원본 절차 (참고)

```bash
curl -N -H 'Accept: text/event-stream' -H 'Content-Type: application/json' \
  -d '{"model":"...","messages":[{"role":"user","content":"안녕"}],"stream":true}' \
  "$BASE_URL/v1/chat/completions" | tee sse-dump.txt
```

이 덤프 하나가 다섯 가지를 한 번에 확정한다. 사내 SSE가 표준 형태인지, 종료 표지가 오는지,
이름 붙은 이벤트를 쓰는지, reasoning 필드 이름이 무엇인지, 인용 태그가 중첩인지 속성인지.
이후 모든 단계의 회귀 시험 fixture로 쓴다. 비용이 가장 싸고 없애는 불확실성이 가장 크다.

## 단계별 산출물

| 단계 | 산출물 | 완료 조건 |
|---|---|---|
| 1 | 패키지 골격, `Bridge`, SSE 파서, `chat_completions` 어댑터, 허브 모델 | 사내 엔드포인트에서 델타가 흐른다 |
| 2 | `StreamMerger` + 개방형 유니온 레지스트리 | 조각난 스트림이 비스트리밍 응답과 같은 최종 객체가 된다 |
| 3 | `compose` + 파이프라인 조립 | 두 단계 매퍼의 flush 순서 시험 통과 |
| 4 | 태그 파서 | `EnhancedCompletionDeltaMapperTest` 이식분 전부 통과 |
| 5 | `CiteVocabulary` + 왕복 | 파싱한 인용을 요청에 되썼을 때 같은 결과로 복원된다 |
| 6 | 동기 API, 문서, 배포 | `uv build` 통과, README 사용 예시 동작 |

1단계 끝에 이미 쓸 수 있는 라이브러리가 된다. 2~5단계는 그 위에 얹는다.

## 이번 범위 밖

다음은 설계 자리만 두고 구현하지 않는다.

- `messages`, `responses`, `generate_content` 어댑터. 벤더 축 확장이므로 추가만 하면 된다.
  사내 agent 어댑터가 통과했으므로 어댑터 경계 설계는 검증됐다. 이 셋은 규격이 더 정연하다
- 사내 챗 서버의 Turn 이벤트 SSE 소비. 재연결과 스냅샷 폴백이 새 요구로 들어온다
- 문서 첨부 어휘. cite 어휘가 자리를 잡았으므로 같은 틀로 만든다

---

# 5. 미결 사항

## 결정됨

| 항목 | 결정 |
|---|---|
| 패키지 이름 | 배포 `enhanced-completion-client`, 모듈 `enhanced_completion` |
| 동기 API | 낸다. `SyncBridge`로 같은 모양 |
| 사내 agent SSE | agent-studio 1.4.1 프론트 번들에서 확정. 2.2절. 어댑터 구현 완료 |
| 인용 태그 문법 | 속성형 `<cite id="d1">본문</cite>`. 모델이 정하지 않으므로 어휘가 프롬프트 지시도 함께 든다 |
| code agent 도구 이벤트 | `tool_use`가 아니라 `agent_activity`. 실행 요청이 아니라 사후 보고다 |
| vLLM 응답 형태 | 로컬 qwen3-8b로 실측 확정. 4절 0단계 |
| 추론 필드 이름 | `reasoning` |

## 남은 것

| 항목 | 필요한 결정 | 막는 단계 |
|---|---|---|
| 사내 LUXIA 실제 응답 | `172.16.100.200:14100`이 닿을 때 라이브 시험 재실행. qwen3-8b와 다른 점이 있는지 | 없음. 지금 구조로 대응 가능 |
| agent SSE 실제 호출 | 이벤트 이름과 프레임 규칙은 확정하고 어댑터를 구현했다. 각 이벤트 본문의 필드 이름은 프론트가 훑는 아홉 경로로 흡수했으나, 실제 서버를 띄워 확인하면 `tool_call`/`sources` 매핑을 좁힐 수 있다 | 없음. 지금 구조로 동작 |
| `tool_call` 의미 | agent가 스스로 실행하는지 HITL 승인을 기다리는지. 후자면 `tool_use`가 맞고 전자면 `agent_activity`로 옮겨야 한다 | 없음. 현재 `tool_use` |
| 챗 서버 Turn 스트림 소비 | 소비하면 재연결·스냅샷 폴백이 새 요구로 들어온다 | 범위 밖 항목 |
| 배포 경로 | PyPI 공개 대 사내 인덱스 대 git 의존 | 6단계 |
| `streambind` 0.1.1 | 합성 연산자 유무. 로컬 캐시에 없어 미확인 | 3단계 (없다고 가정하고 진행 가능) |
