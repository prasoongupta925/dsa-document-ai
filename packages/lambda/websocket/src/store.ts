import {
  type AttributeValue,
  BatchWriteItemCommand,
  DeleteItemCommand,
  DynamoDBClient,
  PutItemCommand,
  QueryCommand,
  type WriteRequest,
} from '@aws-sdk/client-dynamodb';
import { KEYS, type Key, META, PREFIX } from './keys.js';

/**
 * WebSocket connection state in DynamoDB (on demand, nothing to pay while
 * idle). Every write sets expires_at = now + 24 h (table TTL), so items a lost
 * $disconnect leaves behind delete themselves; API Gateway closes a
 * connection after 2 h at most.
 */
export const TTL_SECONDS = 24 * 60 * 60;

export const ddb = new DynamoDBClient({});

type Item = Record<string, AttributeValue>;

const BATCH_SIZE = 25;
const MAX_BATCH_ATTEMPTS = 5;

function tableName(): string {
  return process.env.WS_CONNECTIONS_TABLE_NAME;
}

function toKey({ pk, sk }: Key): Item {
  return { pk: { S: pk }, sk: { S: sk } };
}

async function put(key: Key, attributes: Record<string, string> = {}) {
  const item: Item = {
    ...toKey(key),
    expires_at: { N: String(Math.floor(Date.now() / 1000) + TTL_SECONDS) },
  };
  for (const [name, value] of Object.entries(attributes)) {
    item[name] = { S: value };
  }
  await ddb.send(new PutItemCommand({ TableName: tableName(), Item: item }));
}

async function remove(key: Key) {
  await ddb.send(
    new DeleteItemCommand({ TableName: tableName(), Key: toKey(key) }),
  );
}

/** All items under one partition key, every page. */
async function queryPartition(pk: string): Promise<Item[]> {
  const items: Item[] = [];
  let startKey: Item | undefined;
  do {
    const page = await ddb.send(
      new QueryCommand({
        TableName: tableName(),
        ConsistentRead: true,
        KeyConditionExpression: '#pk = :pk',
        ExpressionAttributeNames: { '#pk': 'pk' },
        ExpressionAttributeValues: { ':pk': { S: pk } },
        ExclusiveStartKey: startKey,
      }),
    );
    items.push(...(page.Items ?? []));
    startKey = page.LastEvaluatedKey;
  } while (startKey);
  return items;
}

/** Deletes the keys, 25 per BatchWriteItem, retrying unprocessed ones. */
async function batchDelete(keys: Key[]) {
  const unique = [
    ...new Map(keys.map((key) => [`${key.pk}\n${key.sk}`, key])).values(),
  ];
  for (let i = 0; i < unique.length; i += BATCH_SIZE) {
    let requests: WriteRequest[] = unique
      .slice(i, i + BATCH_SIZE)
      .map((key) => ({ DeleteRequest: { Key: toKey(key) } }));
    for (let attempt = 1; requests.length > 0; attempt++) {
      const { UnprocessedItems } = await ddb.send(
        new BatchWriteItemCommand({
          RequestItems: { [tableName()]: requests },
        }),
      );
      requests = UnprocessedItems?.[tableName()] ?? [];
      if (requests.length > 0 && attempt >= MAX_BATCH_ATTEMPTS) {
        console.warn(
          `${requests.length} connection items not deleted, left to TTL`,
        );
        break; // go on with the next chunk
      }
      if (requests.length > 0) {
        await new Promise((resolve) => setTimeout(resolve, 50 * 2 ** attempt));
      }
    }
  }
}

export async function addConnection(
  connectionId: string,
  userSub: string,
  username: string,
) {
  await Promise.all([
    put(KEYS.conn(connectionId), { userSub, username }),
    put(KEYS.userConn(username, connectionId)),
  ]);
}

export async function subscribe(connectionId: string, projectId: string) {
  await Promise.all([
    put(KEYS.projectConn(projectId, connectionId)),
    put(KEYS.connProject(connectionId, projectId)),
  ]);
}

export async function unsubscribe(connectionId: string, projectId: string) {
  await Promise.all([
    remove(KEYS.projectConn(projectId, connectionId)),
    remove(KEYS.connProject(connectionId, projectId)),
  ]);
}

/**
 * Removes every trace of a connection: its META and PROJ# items and the
 * USER#/PROJ# entries that point back at it (one query, batched deletes).
 */
export async function removeConnection(connectionId: string) {
  const keys: Key[] = [];
  for (const item of await queryPartition(KEYS.conn(connectionId).pk)) {
    const sk = item.sk?.S;
    if (!sk) continue;
    keys.push({ pk: KEYS.conn(connectionId).pk, sk });
    const username = item.username?.S;
    if (sk === META && username) {
      keys.push(KEYS.userConn(username, connectionId));
    } else if (sk.startsWith(PREFIX.project)) {
      keys.push(
        KEYS.projectConn(sk.slice(PREFIX.project.length), connectionId),
      );
    }
  }
  await batchDelete(keys);
}
