# IDP Agent

## 로컬 실행 방법

```bash
# 1. 의존성 설치
uv sync

# 2. 가상환경 활성화
source .venv/bin/activate

# 3. 서버 실행
python -m main
```

서버가 `http://localhost:8080`에서 실행됩니다.

## 테스트 요청

```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: test-session" \
  -d '{"prompt": "1 + 2는 뭐야?", "session_id": "test-123", "project_id": "project-123"}'
```

## Streaming Event 형식

Agent는 스트리밍 응답을 반환하며, 각 이벤트는 다음 형식을 따릅니다.

### Event Types

| type | 설명 | 예시 |
|------|------|------|
| `text` | 텍스트 스트리밍 청크 | `{"type": "text", "content": "안녕하세요"}` |
| `tool_use` | 도구 사용 시작 | `{"type": "tool_use", "name": "search_documents"}` |
| `complete` | 응답 완료 | `{"type": "complete"}` |

### 클라이언트 처리 예시

```typescript
const response = await fetch('/invocations', { ... });
const reader = response.body.getReader();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  
  const event = JSON.parse(new TextDecoder().decode(value));
  
  switch (event.type) {
    case 'text':
      // 텍스트 출력
      appendText(event.content);
      break;
    case 'tool_use':
      // 도구 사용 표시 (예: "검색 중...")
      showToolIndicator(event.name);
      break;
    case 'complete':
      // 완료 처리
      finishResponse();
      break;
  }
}
```
