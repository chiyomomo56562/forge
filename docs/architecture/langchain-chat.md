# LangChain 대화 runtime

Forge의 대화 실행 경로는 `ReceiveMessageService` application facade와
`forge.runtime.LangGraphConversationRuntime`으로 구성된다.

`LangGraphConversationRuntime`은 `MessagesState`와 `InMemorySaver`를 사용한다.
`conversation_id`는 LangGraph의 `thread_id`가 되어, 같은 프로세스에서 같은 대화의
user/assistant 문맥을 재사용한다. 프로세스가 종료되면 이 상태는 사라진다.

`system_instruction`은 저장되는 메시지가 아니다. 호출마다 runtime context로 전달되고,
해당 모델 호출 직전에만 `SystemMessage`로 주입된다. 따라서 다음 호출은 새 system
instruction을 지정할 수 있으며, 이전 instruction이 checkpoint에 누적되지 않는다.

현재 runtime은 text-only assistant 응답만 지원한다. tool calling, L3 skill 실행, MCP
연결, 장기 기억, database-backed checkpoint, streaming은 포함하지 않는다.
