import {
  type AttributeValue,
  BatchWriteItemCommand,
  DynamoDBClient,
  QueryCommand,
  ScanCommand,
  type WriteRequest,
} from '@aws-sdk/client-dynamodb';
import { KEYS, type Key, META, PREFIX } from './keys.js';

/**
 * Reads of the WebSocket connections table (written by the websocket
 * Lambdas, see keys.ts) and the cleanup of a gone connection.
 */
const ddb = new DynamoDBClient({});

type Item = Record<string, AttributeValue>;

const BATCH_SIZE = 25;
const MAX_BATCH_ATTEMPTS = 5;

function tableName(): string {
  return process.env.WS_CONNECTIONS_TABLE_NAME;
}

function toKey({ pk, sk }: Key): Item {
  return { pk: { S: pk }, sk: { S: sk } };
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

/** Connection ids of one user (USER#<username> → CONN#<id> items). */
export async function getConnectionIdsByUsername(
  username: string,
): Promise<string[]> {
  const items = await queryPartition(`${PREFIX.user}${username}`);
  return items
    .map((item) => item.sk?.S ?? '')
    .filter((sk) => sk.startsWith(PREFIX.conn))
    .map((sk) => sk.slice(PREFIX.conn.length));
}

/** Every open connection: the META items (a scan; the table stays small). */
export async function getAllConnectionIds(): Promise<string[]> {
  const connectionIds: string[] = [];
  let startKey: Item | undefined;
  do {
    const page = await ddb.send(
      new ScanCommand({
        TableName: tableName(),
        ConsistentRead: true,
        FilterExpression: '#sk = :meta AND begins_with(#pk, :conn)',
        ProjectionExpression: '#pk',
        ExpressionAttributeNames: { '#pk': 'pk', '#sk': 'sk' },
        ExpressionAttributeValues: {
          ':meta': { S: META },
          ':conn': { S: PREFIX.conn },
        },
        ExclusiveStartKey: startKey,
      }),
    );
    for (const item of page.Items ?? []) {
      const pk = item.pk?.S;
      if (pk) connectionIds.push(pk.slice(PREFIX.conn.length));
    }
    startKey = page.LastEvaluatedKey;
  } while (startKey);
  return connectionIds;
}

/**
 * Removes every trace of a connection: its META and PROJ# items and the
 * USER#/PROJ# entries that point back at it (one query, batched deletes).
 * Same as the $disconnect handler (packages/lambda/websocket/src/store.ts).
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
