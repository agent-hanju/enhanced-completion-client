# completion-bridge 0.4.1

`0.4.0` 대비 Anthropic Messages 요청의 도구 선택 옵션을 수정한다.

`ToolChoice(mode="none")`과 `parallel_tool_calls`를 함께 설정했을 때, `tool_choice`에
지원하지 않는 `disable_parallel_tool_use` 필드를 추가하던 문제를 수정했다.
이제 `{"type": "none"}`만 전송한다. `auto`·`required`·`named` 모드의 병렬 호출 설정은 유지한다.

공개 API와 의존성은 변경하지 않는다. 회귀 테스트는 `none`에서 해당 필드를 생략하는지,
`auto`에서는 `parallel_tool_calls=False`를 `disable_parallel_tool_use=True`로 변환하는지 확인한다.
