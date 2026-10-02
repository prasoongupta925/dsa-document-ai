/**
 * Items of the WebSocket connections table (WS_CONNECTIONS_TABLE_NAME):
 * string keys pk/sk, TTL on expires_at.
 *
 * | pk               | sk               | attributes        | was (Valkey)                |
 * |------------------|------------------|-------------------|-----------------------------|
 * | CONN#<connId>    | META             | userSub, username | ws:conn:<id> "sub:username" |
 * | CONN#<connId>    | PROJ#<projectId> |                   | ws:conn:<id>:projects set   |
 * | PROJ#<projectId> | CONN#<connId>    |                   | ws:project:<pid> set        |
 * | USER#<username>  | CONN#<connId>    |                   | ws:username:<name> set      |
 *
 * The same layout is used by websocket-broker and workflow-stream.
 */
export interface Key {
  pk: string;
  sk: string;
}

export const PREFIX = {
  conn: 'CONN#',
  project: 'PROJ#',
  user: 'USER#',
} as const;

export const META = 'META';

export const KEYS = {
  conn: (connectionId: string): Key => ({
    pk: `${PREFIX.conn}${connectionId}`,
    sk: META,
  }),
  connProject: (connectionId: string, projectId: string): Key => ({
    pk: `${PREFIX.conn}${connectionId}`,
    sk: `${PREFIX.project}${projectId}`,
  }),
  projectConn: (projectId: string, connectionId: string): Key => ({
    pk: `${PREFIX.project}${projectId}`,
    sk: `${PREFIX.conn}${connectionId}`,
  }),
  userConn: (username: string, connectionId: string): Key => ({
    pk: `${PREFIX.user}${username}`,
    sk: `${PREFIX.conn}${connectionId}`,
  }),
};
