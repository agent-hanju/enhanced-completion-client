# 모델·도구 반복 실행

`Bridge.run()`은 `HubRequest`와 비동기 도구 콜백으로 모델 호출을 반복한다.
정책·사용자 문맥·도구 서버·작업 생성은 소비 앱이 담당한다. 실행 등록소나 사용자 승인 API는 없다.
`Bridge.stream()`과 `complete()`의 단일 모델 요청 계약은 그대로 유지한다.

## 기본 사용

```python
from completion_bridge import Execution, HubMessage, HubRequest, ToolDefinition, ToolOutput, ToolResultBlock

request = HubRequest(
    messages=[HubMessage.user("자료를 찾아 요약해줘")],
    tools=[ToolDefinition(name="lookup", input_schema={"type": "object"})],
)

async def execute_tool(name, arguments):
    # tool_manager와 context는 소비 앱에서 준비한 객체다.
    # 여기서 호출하는 execute는 소비 앱이 제공하는 어댑터 메서드다.
    return await tool_manager.execute(name, arguments, context)

run = await bridge.run(request, execute_tool=execute_tool)
print(run.messages)
print(run.tasks)  # list[HitlDraft]
```

소비 앱의 콜백은 `(name: str, arguments: dict[str, Any])`만 받고 `ToolOutput`을 반환한다.
콜백은 클로저로 정책·프로필·사용자·대화 문맥·도구 관리자를 참조할 수 있다.
라이브러리가 그 문맥을 복사하거나 해석하지 않는다.

현재 `common-hitl-chat`의 실제 관리자는 `execute_runtime(context, calls)` 형태이므로
위의 `execute(name, arguments, context)`를 직접 제공하지 않는다. 연동 시 소비 앱에서
입력과 반환값을 변환하는 어댑터를 구현해야 한다. 이 변경은 해당 앱을 수정하지 않는다.
기존 관리자가 사용자 입력을 기다린다면, 미해결 기록을 반환하는 동작도 앱에서 마련해야 한다.

## 델타와 실행 결과

```python
execution = bridge.run(request, execute_tool=execute_tool, max_tool_calls=32)
async with execution:
    async for delta in execution:
        print(execution.request_index, delta.text)
run = execution.result
```

첫 `await` 또는 비동기 소비에서 실행을 시작한다. `run()` 호출 시 요청을 중첩 필드까지
복사하므로 이후 원본 요청을 수정해도 실행 입력은 바뀌지 않는다. 모델·도구 정의·생성 설정·
`params`는 매 모델 요청에 적용한다. Bridge에 설정된 모델과 생성 설정도 기존 우선순위를 따른다.
`params`에 `messages`, `tools`, `model`, `hyperparameters`를 중복 지정하면 거부한다.
요청별 응답은 독립적으로 병합한다. 서로 다른 모델 요청의 델타를 한 응답으로 합치지 않는다.

| API | 내용 |
|---|---|
| `await execution` / `await execution.collect()` | 델타를 끝까지 소비하고 `Run` 반환 |
| `execution.result` | 정상 종료 후 `Run`; 종료 전에는 `StreamNotFinished` |
| `execution.partial` | 진행 중 또는 실패·취소까지 확보한 `Run` |
| `execution.request_index` | 현재 모델 요청 번호, 0부터 시작 |
| `run.request` | 독립 복사한 초기 `HubRequest` |
| `run.messages` | 입력과 생성 메시지를 합친 대화 이력 |
| `run.generated_messages` | 이번 실행에서 생성한 assistant·tool 메시지 |
| `run.responses` | 완료한 요청별 `HubResponse`와 사용량 |
| `run.active_response` | 조회 시에만 복사하는 현재 모델 응답. 종료 후에는 마지막 스냅샷 |
| `run.tasks` | 호출 순서의 `list[HitlDraft]`; 실제 작업은 각 `draft.task` |
| `run.status` | `completed`, `hitl_required`, `limit_reached`, `error`, `cancelled`; 진행 중에는 `pending`/`running` |
| `run.final_result` | 콜백이 명시적으로 최종 답변으로 지정한 도구 결과 |

동시에 여러 소비자가 실행 객체를 진행할 수 없다. 소비 중 `aclose()`도 거부한다.
실행을 중단하려면 소비 태스크를 취소하고 종료를 기다린다. 조기 반복 종료에는 반드시
`async with` 또는 `execution.aclose()`로 모델 연결을 정리한다.

## 콜백 반환 계약

```python
async def execute_tool(name, arguments):
    value = await actual_tool(name, arguments)
    return ToolOutput(
        execution=Execution(data=value),
        result=ToolResultBlock(content="모델에게 제공할 결과"),
    )
```

| `ToolOutput` 필드 | 의미 |
|---|---|
| `result` | 후속 모델에 제공하도록 승인된 `ToolResultBlock`. 없으면 `hitl_required`로 종료 |
| `draft` | 선택적인 `HitlDraft`. 콜백의 승인·검토·원본 기록을 보존 |
| `execution` | 선택적인 원본 `Execution`. 제공하면 반환 기록의 execution에 적용 |
| `task` | 선택적인 `asyncio.Task`. 반환 기록의 task에 보존하고 background를 표시 |
| `final` | 기본 `False`. `True`이면 result를 최종 결과로 사용하고 후속 요청 없이 종료 |

라이브러리는 정책을 판정하거나 거절 결과를 생성하지 않는다. 콜백이 전달 가능한
`result`를 제공했는지가 후속 요청의 기준이다. 승인·검토가 필요하면 **result를 제공하지 않아야 한다**.
실행 후 승인이 필요한 실제 결과는 `draft.execution` 또는 `execution`에 보관할 수 있다.
거절이나 예상된 도구 실패를 모델에 전달하려면 콜백이 오류 `result`를 만들어 반환한다.
처리하지 않은 콜백 예외는 자동 오류 메시지로 바꾸지 않고 부분 실행 상태와 함께 전파한다.

```python
# 소비 앱에서 얻은 chat_draft와 model_result를 변환하는 예
output = ToolOutput(
    draft=HitlDraft.model_validate(chat_draft.model_dump()),
    result=model_result,  # 사용자 판단이 필요하면 None
)
```

콜백은 원본 모델 호출 ID와 블록 순서를 받지 않는다. 콜백이 제공한 `draft.call`과
`result.tool_use_id`·`result.name`은 라이브러리가 원본 호출 정보로 설정한 사본으로 보관한다.
외부 관리자의 영속 기록 ID와 모델의 도구 호출 ID를 연결해야 한다면 소비 앱에서 별도 연동이 필요하다.
반환한 원본 draft와 result는 수정하지 않으며 Task 핸들은 복사하지 않는다.

한 응답에 여러 호출이 있으면 전체 호출 ID와 인자를 먼저 검증하고 기록을 만든다.
그 뒤 순서대로 콜백을 호출한다. result가 없는 호출에서 종료하며 이후 호출은 미실행 기록으로
반환한다. 앞선 결과는 보존하지만 필요한 모든 결과가 준비되지 않으면 후속 모델 요청을 하지 않는다.
`max_tool_calls`는 전체 실행의 콜백 호출 상한이며 기본값은 32다. 0도 허용한다.
상한에 도달하면 남은 호출을 실행하거나 추가 모델 요청을 하지 않고 `limit_reached`로 종료한다.
`final=True`는 result를 요구하며 같은 응답의 나머지 호출은 실행하지 않는다.

## 백그라운드 작업과 취소

콜백이 작업을 직접 만들고 `task`로 반환한다. 런타임은 작업을 생성하거나 자동 대기하지 않는다.
후속 모델 요청을 계속하려면 함께 접수 `result`를 반환한다. result가 없으면 작업 핸들을
보존한 채 `hitl_required`로 종료한다. 백그라운드 결과를 후속 모델에 자동 재전달하지 않는다.

`HitlDraft.task`는 프로세스·이벤트 루프 내부 핸들이며 `model_dump()`와 JSON에서 제외한다.
기존 HITL 필드에 `result`와 `task`를 추가했으며 task 자체를 영속 저장하거나 이전할 수는 없다.
작업의 상태와 결과는 `draft.task.done()`·`await draft.task` 등으로 확인한다.

정상 종료·조기 종료·오류·취소 모두 이미 반환된 외부 작업을 자동 취소하지 않는다.
소비 앱은 정상 `Run` 또는 예외의 `partial`에서 작업을 인계받아 관리한다.
`await run.aclose()`를 명시적으로 호출하면 전달받은 작업을 취소하고 완료를 기다린다.
콜백이 반환하기 전에 생성한 작업과 별도 등록소에 보관한 작업은 콜백·소비 앱이 정리해야 한다.
진행 중인 콜백 await에는 일반적인 asyncio 취소가 전달된다.

모델 또는 콜백 실패는 `RuntimeErrorWithPartial`로 전파하며 `__cause__`에 원래 예외,
`partial`에 확보한 응답과 기록을 보관한다. 취소는 `RuntimeCancelled`로 전달한다.
오류·취소를 정상 완료로 표현하지 않는다. `execution.aclose()`는 모델 연결만 정리한다.

## 실행 추적과 제거한 API

실행 하나가 `runtime` span이고, 모델 요청(`chat {model}`)과 콜백 실행(`execute_tool {name}`)은 그
하위 span이다. 콜백 안에서 만든 기록과 span은 도구 span 아래에 들어간다.
운영 로그에는 본문·도구 인자·결과·예외 메시지를 기록하지 않는다.
출력 설정과 보관 정책은 [로그 문서](Wire-Logging.md)를 따른다.

별도 `CompletionRuntime`, `ToolExecutor`, `PolicyEvaluator`, `HitlTask`는 제거했다.
메시지·도구 목록·정책 객체를 받던 run 호출은 `HubRequest`와 `execute_tool` 콜백으로 변경해야 한다.
