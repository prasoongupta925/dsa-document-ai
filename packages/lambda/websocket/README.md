# WebSocket Messages

## Actions

### sessions

세션 생성/수정/삭제 이벤트

```json
{
  "action": "sessions",
  "data": {
    "event": "created" | "updated" | "deleted",
    "sessionId": "string",
    "sessionName": "string",
    "timestamp": "string (ISO 8601)"
  }
}
```

### artifacts

아티팩트 생성/수정/삭제 이벤트

```json
{
  "action": "artifacts",
  "data": {
    "event": "created" | "updated" | "deleted",
    "artifactId": "string",
    "artifactFileName": "string",
    "timestamp": "string (ISO 8601)"
  }
}
```

---

# Connection store (DynamoDB)

One on-demand table (`idp-v2-ws-connections`, name in `WS_CONNECTIONS_TABLE_NAME`,
SSM `/idp-v2/websocket/connections-table-name`). Every item gets
`expires_at` = now + 24 h (table TTL), so entries of a lost `$disconnect`
delete themselves. Layout in `src/keys.ts`:

| pk | sk | Attributes | Meaning |
|----|----|------------|---------|
| `CONN#{connectionId}` | `META` | `userSub`, `username` | connection → user |
| `CONN#{connectionId}` | `PROJ#{projectId}` | | projects the connection follows |
| `PROJ#{projectId}` | `CONN#{connectionId}` | | connections following a project |
| `USER#{username}` | `CONN#{connectionId}` | | connections of a user |

## Usage (`src/store.ts`)

```typescript
await addConnection(connectionId, userSub, username); // $connect
await subscribe(connectionId, projectId);             // {"action":"subscribe"}
await unsubscribe(connectionId, projectId);           // {"action":"unsubscribe"}
await removeConnection(connectionId);                 // $disconnect: all of the above
```

Readers: `websocket-broker` queries `USER#{username}` (or scans the `META`
items to reach every connection); `workflow-stream` queries `PROJ#{projectId}`.
Both call `removeConnection` when API Gateway answers `GoneException`.
