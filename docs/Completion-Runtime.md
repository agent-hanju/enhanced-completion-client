# Completion Runtime

`CompletionRuntime`은 기존 `Bridge`를 사용해 모델 호출과 도구 실행을 반복한다.
필수 의존성은 기존 `httpx`, `pydantic`을 유지하며 DB나 웹 서버를 요구하지 않는다.

## 기본 사용

```python
from completion_bridge import (
    CompletionRuntime, Execution, HitlDecision, ToolCall, ToolDefinition,
    ToolOutput, ToolPolicy, ToolResultBlock,
)


class Tools:
    definitions = [
        ToolDefinition(name="lookup", input_schema={"type": "object"}),
    ]

    async def execute(self, call: ToolCall) -> ToolOutput:
        return ToolOutput(
            execution=Execution(data={"original": "전체 실행 결과"}),
            result=ToolResultBlock(content="모델에 제공할 요약"),
        )


class Policy:
    async def approve(self, call):
        return HitlDecision(decision=ToolPolicy.ALLOW, source="default")

    async def review(self, call, execution):
        return HitlDecision(decision=ToolPolicy.ALLOW, source="default")


runtime = CompletionRuntime(bridge=bridge, max_tool_calls=32)
execution = runtime.run(messages, tools=Tools(), policy=Policy())
async with execution:
    async for delta in execution:
        print(execution.request_index, delta.text)
run = execution.result
```

`messages`는 `Sequence[HubMessage]`다. `run()`을 호출할 때 중첩 블록과 도구 정의를
복사하며 실제 실행은 첫 `__anext__()`에서 시작한다. 실행 객체는 한 번만 소비한다.
다음 호출도 같은 스트림 실행을 일괄 소비한다.

```python
run = await runtime.complete(messages, tools=Tools(), policy=Policy())
```

모델 및 생성 설정은 주입한 Bridge의 설정을 사용한다. Bridge와 실행기 연결의 수명은
호출자가 관리한다. 진행 중인 백그라운드 작업이 있으면 해당 연결을 유지해야 한다.

## 결과와 도구 작업

| API | 내용 |
|---|---|
| `run.messages` | 입력 메시지의 독립 복사본 |
| `run.generated_messages` | 이번 실행의 모델 응답 및 모델용 도구 결과 메시지 |
| `run.responses` | 완료된 요청별 응답. 사용량·중단 및 차단 사유 포함 |
| `run.active_response` | 아직 완료되지 않은 요청에서 확보한 부분 응답 |
| `run.tasks` | 호출 순서의 `HitlTask` 목록 |
| `run.status` | `pending`, `running`, `completed`, `approval_required`, `limit_reached`, `error`, `cancelled` |
| `run.final_result` | 도구 결과가 최종 답변을 대체한 경우의 결과 블록 |
| `await run.aclose()` | 자식 실행을 포함한 모든 도구 작업 취소 및 회수 |

`Run.tasks → HitlTask.draft → HitlDraft` 관계로 기록과 작업을 함께 제공한다.
별도의 HITL 목록이나 백그라운드 작업 목록을 중복 보관하지 않는다.

```python
for task in run.tasks:
    print(task.draft.call, task.draft.background, task.status)
    if task.status == "running":
        draft = await task.wait()
```

- `task.draft`: 참고 프로젝트의 필드 구조를 유지한 호출·정책·실행 기록.
- `task.result`: 검토를 통과한 모델용 결과 또는 정책 거절 결과.
- `task.failure`: 정책 평가 실패 등 작업을 중단한 예외.
- `task.child_run`: 에이전트의 독립 실행 결과. 일반 도구는 `None`.
- `task.status`: `pending`, `running`, `completed`, `approval_required`, `error`, `cancelled`.
- `await task.wait()`: 실제 실행과 검토 완료 후 같은 `HitlDraft`를 반환한다.
  미실행 호출은 현재 기록을 반환한다. 작업 오류는 전파한다.
  이 대기를 취소해도 실제 작업은 취소하지 않는다.
- `task.cancel()`: 실제 태스크에 취소를 요청한다. 정리가 끝나려면 `wait()`로
  취소 예외까지 회수하거나 `run.aclose()`를 사용한다.

`HitlDraft`는 저장 가능한 데이터이며 이벤트 루프의 태스크를 포함하지 않는다.
`Run`과 `HitlTask`는 프로세스 내부 실행 객체이며 저장 모델이 아니다.

## 승인·검토와 여러 호출

한 응답의 모든 호출을 먼저 기록하고 블록 순서대로 판정 및 실행한다.
`ASK`를 만나면 이후 호출은 `pending`으로 남겨 반환한다.

| 단계 | ALLOW | ASK | DENY |
|---|---|---|---|
| 실행 전 | 도구 실행 | 실행 없이 메인 종료 | 거절 결과를 모델에 제공 |
| 실행 후 | 모델용 결과 사용 | 원본만 보존하고 메인 종료 | 원본을 보존하고 거절 결과만 모델에 제공 |

실행 후 정책은 `Execution` 원본을 검토한다. `ToolOutput.result`가 실제 모델에 제공할
내용이며 `Execution.data`, `structured_content`, `meta`를 자동으로 모델에 전달하지 않는다.
실행기와 정책에는 원본 호출 기록의 독립 복사본을 전달한다.
실행 결과 블록의 호출 ID와 이름은 런타임이 원본 호출 값으로 지정한다.

실행기에서 발생한 일반 `Exception`은 도구 실패로 기록한다. 예외 메시지 대신 예외 타입을
`draft.error`, `Execution.error`에 기록하고 모델에는 일반 실패 메시지를 제공한다.
실패 결과에도 실행 후 정책을 적용한다. 등록되지 않은 도구는 실행하지 않고 오류 결과를 제공한다.
정책 평가 및 모델 통신 실패는 메인 실행 오류다. 자식 런타임 오류 역시 도구 실패로 변환하지 않는다.

사용자 결정을 기다리거나 같은 실행을 재개하는 API는 없다.

## 요청 구분·제한·답변 대체

델타 타입은 기존 `HubResponse`다. 소비 중 `execution.request_index`가 현재 요청의
0부터 시작하는 번호다. 여러 요청의 델타를 하나의 응답으로 병합하지 않는다.

`max_tool_calls`는 기본 32이며 0 이상의 정수다. 거절·알 수 없는 호출을 포함한
호출 순서 기준으로 적용한다. 상한에 도달한 호출은 실행하지 않고 `limit_reached`로
종료한다. 이후 호출도 기록하며 추가 최종 모델 요청은 하지 않는다.

실행기가 `ToolOutput(final=True, ...)`를 반환하고 실행 후 판정이 `ALLOW`이면
해당 모델용 결과를 `run.final_result`로 제공하고 종료한다. 같은 응답의 나머지 호출은
미실행 기록으로 유지한다. 백그라운드 작업의 `final`은 메인 결과를 변경하지 않는다.

## 백그라운드 실행

```python
runtime = CompletionRuntime(bridge=bridge, background_tools=["lookup"])
run = await runtime.complete(messages, tools=Tools(), policy=Policy())
try:
    for task in run.tasks:
        if task.draft.background:
            await task.wait()
finally:
    await run.aclose()
```

`background_tools`는 도구 정의에 존재하는 이름만 허용한다. 실행 전 승인 판정 후
태스크를 시작하고 모델에는 접수 결과를 제공한다. 메인 종료 후에도 작업은 진행한다.
실제 결과와 실행 후 정책은 해당 `task.draft`에서 확인한다. 작업이 승인 필요 또는
오류로 종료되어도 이미 완료한 메인 `Run.status`나 메시지는 변경하지 않는다.
메인 모델에 완료 결과를 자동으로 다시 전달하지 않는다.

## 오류·취소·조기 종료

- 정상 종료 후 `execution.result`를 제공한다. 진행 중에는 `StreamNotFinished`가 발생한다.
- `execution.partial`은 진행 중·오류·취소 후에도 같은 `Run`을 제공한다.
- 메인 실패는 `RuntimeErrorWithPartial`로 전파한다. `.partial`은 부분 실행 결과이고
  `.__cause__`는 원래 예외다.
- 취소는 `asyncio.CancelledError`의 하위 타입인 `RuntimeCancelled`로 전파하며
  `.partial`을 제공한다. `complete()`에서도 동일하다.
- 조기 반복 종료는 `async with execution` 또는 `await execution.aclose()`를 사용한다.
  `async for`의 `break`만으로 명시적 자원 정리를 보장하지 않는다.
- 오류·취소·조기 종료 시 모델 스트림을 정리하고 자식 실행을 포함한 도구 작업을 취소·회수한다.
  이미 확보한 실행 기록과 발생한 외부 효과는 취소된 것으로 변경하지 않는다.
- 정상 종료 이후에는 호출자가 `Run`과 각 작업의 핸들을 관리한다.

## 자식 에이전트

`AgentToolExecutor`는 하나의 도구 정의를 독립 자식 런타임에 연결하는 실행기다.
부모와 자식 메시지·응답·작업은 각각의 `Run`에 보존한다.

```python
from completion_bridge import AgentToolExecutor, HubMessage, ToolDefinition

agent = AgentToolExecutor(
    definition=ToolDefinition(name="research", input_schema={"type": "object"}),
    runtime=child_runtime,
    messages=lambda call: [HubMessage.user(str(call.arguments))],
    tools=child_tools,
    policy=child_policy,
)
run = await runtime.complete(messages, tools=agent, policy=Policy())
```

최종 모델 응답 본문 또는 답변 대체 결과를 부모 모델용 결과로 사용한다.
자식이 승인 필요로 종료하면 부모 작업에 `review=ASK`를 기록하고 원본 자식 기록은
`task.child_run.tasks`로 제공한다. 부모도 일반 호출이면 승인 필요로 종료한다.
백그라운드 부모 작업이면 해당 작업만 승인 필요로 종료한다.
자식의 호출 제한 종료는 자식 상태로 보존하고 부모에는 확보한 답변을 반환한다.
