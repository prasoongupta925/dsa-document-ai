import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import type {
  APIGatewayProxyEvent,
  APIGatewayProxyHandler,
  Context,
} from 'aws-lambda';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { connectHandler } from './connect.js';
import { defaultHandler } from './default.js';
import { disconnectHandler } from './disconnect.js';
import { TTL_SECONDS } from './store.js';
import { FakeDynamoDB } from './test/fake-dynamodb.js';

const WS_TABLE = 'idp-v2-ws-connections';
const BACKEND_TABLE = 'backend-table';
const NOW = new Date('2026-09-30T12:00:00Z');
const EXPIRES_AT = String(NOW.getTime() / 1000 + 24 * 60 * 60);
const PROVIDER =
  'cognito-idp.ap-south-1.amazonaws.com/ap-south-1_pool,' +
  'cognito-idp.ap-south-1.amazonaws.com/ap-south-1_pool:CognitoSignIn:';

let db: FakeDynamoDB;

beforeEach(() => {
  process.env.WS_CONNECTIONS_TABLE_NAME = WS_TABLE;
  process.env.BACKEND_TABLE_NAME = BACKEND_TABLE;
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  db = new FakeDynamoDB({
    [WS_TABLE]: ['pk', 'sk'],
    [BACKEND_TABLE]: ['PK', 'SK'],
  });
  db.seed(BACKEND_TABLE, [
    {
      PK: { S: 'USERSUB#sub-1' },
      SK: { S: 'META' },
      data: { M: { username: { S: 'alice' } } },
    },
  ]);
  vi.spyOn(DynamoDBClient.prototype, 'send').mockImplementation((command) =>
    db.send(command),
  );
  vi.spyOn(console, 'log').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function call(
  handler: APIGatewayProxyHandler,
  requestContext: Record<string, unknown>,
  body: string | null = null,
) {
  const event = { requestContext, body } as unknown as APIGatewayProxyEvent;
  return handler(event, {} as Context, () => undefined);
}

const connect = (connectionId: string, sub = 'sub-1') =>
  call(connectHandler, {
    connectionId,
    identity: { cognitoAuthenticationProvider: `${PROVIDER}${sub}` },
  });

const send = (connectionId: string, message: unknown) =>
  call(
    defaultHandler,
    { connectionId },
    typeof message === 'string' ? message : JSON.stringify(message),
  );

const disconnect = (connectionId: string) =>
  call(disconnectHandler, { connectionId });

/** The connections table as [pk, sk] pairs. */
const rows = () => db.items(WS_TABLE).map((item) => [item.pk.S, item.sk.S]);

describe('connect', () => {
  it('stores the connection and its user for 24 hours', async () => {
    expect(TTL_SECONDS).toBe(24 * 60 * 60);

    await expect(connect('conn-1')).resolves.toEqual({
      statusCode: 200,
      body: 'Connected',
    });

    expect(db.items(WS_TABLE)).toEqual([
      {
        pk: { S: 'CONN#conn-1' },
        sk: { S: 'META' },
        expires_at: { N: EXPIRES_AT },
        userSub: { S: 'sub-1' },
        username: { S: 'alice' },
      },
      {
        pk: { S: 'USER#alice' },
        sk: { S: 'CONN#conn-1' },
        expires_at: { N: EXPIRES_AT },
      },
    ]);
    expect(db.calls.find((c) => c.command === 'GetItemCommand')?.input).toEqual(
      {
        TableName: BACKEND_TABLE,
        Key: { PK: { S: 'USERSUB#sub-1' }, SK: { S: 'META' } },
      },
    );
  });

  it('stores nothing for a user the backend does not know', async () => {
    await expect(connect('conn-1', 'sub-unknown')).resolves.toMatchObject({
      statusCode: 200,
    });
    expect(rows()).toEqual([]);
  });

  it('stores nothing without a Cognito identity', async () => {
    await call(connectHandler, { connectionId: 'conn-1', identity: {} });
    expect(db.calls).toEqual([]);
  });
});

describe('default (subscribe / unsubscribe)', () => {
  it('keeps the project and connection sides in step', async () => {
    await send('conn-1', { action: 'subscribe', projectId: 'proj-1' });

    expect(db.items(WS_TABLE)).toEqual([
      {
        pk: { S: 'CONN#conn-1' },
        sk: { S: 'PROJ#proj-1' },
        expires_at: { N: EXPIRES_AT },
      },
      {
        pk: { S: 'PROJ#proj-1' },
        sk: { S: 'CONN#conn-1' },
        expires_at: { N: EXPIRES_AT },
      },
    ]);

    await send('conn-1', { action: 'unsubscribe', projectId: 'proj-1' });
    expect(rows()).toEqual([]);
  });

  it('ignores anything else', async () => {
    for (const message of [
      'not json',
      { action: 'ping' },
      { action: 'subscribe' },
    ]) {
      await expect(send('conn-1', message)).resolves.toEqual({
        statusCode: 200,
        body: 'OK',
      });
    }
    expect(db.calls).toEqual([]);
  });

  it('still answers 200 when DynamoDB fails', async () => {
    vi.mocked(DynamoDBClient.prototype.send).mockRejectedValue(
      new Error('throttled'),
    );
    await expect(
      send('conn-1', { action: 'subscribe', projectId: 'proj-1' }),
    ).resolves.toEqual({ statusCode: 200, body: 'OK' });
  });
});

describe('disconnect', () => {
  it('removes only that connection, its user entry and subscriptions', async () => {
    await connect('conn-1');
    await connect('conn-2');
    await send('conn-1', { action: 'subscribe', projectId: 'proj-1' });
    await send('conn-1', { action: 'subscribe', projectId: 'proj-2' });
    await send('conn-2', { action: 'subscribe', projectId: 'proj-1' });

    await expect(disconnect('conn-1')).resolves.toEqual({
      statusCode: 200,
      body: 'Disconnected',
    });

    expect(rows()).toEqual([
      ['CONN#conn-2', 'META'],
      ['CONN#conn-2', 'PROJ#proj-1'],
      ['PROJ#proj-1', 'CONN#conn-2'],
      ['USER#alice', 'CONN#conn-2'],
    ]);
  });

  it('follows every query page and deletes 25 keys per batch', async () => {
    await connect('conn-1');
    for (let i = 0; i < 30; i++) {
      await send('conn-1', { action: 'subscribe', projectId: `proj-${i}` });
    }
    expect(rows()).toHaveLength(62);

    await disconnect('conn-1');

    expect(rows()).toEqual([]);
    expect(db.count('QueryCommand')).toBe(16); // 31 items, 2 per page
    expect(db.count('BatchWriteItemCommand')).toBe(3); // 62 keys
  });

  it('retries unprocessed deletes', async () => {
    await connect('conn-1');
    await send('conn-1', { action: 'subscribe', projectId: 'proj-1' });
    db.unprocessed = 3;

    await disconnect('conn-1');

    expect(rows()).toEqual([]);
    expect(db.count('BatchWriteItemCommand')).toBe(2);
  });

  it('leaves a chunk that stays unprocessed to the TTL, deletes the rest', async () => {
    await connect('conn-1');
    for (let i = 0; i < 30; i++) {
      await send('conn-1', { action: 'subscribe', projectId: `proj-${i}` });
    }
    db.unprocessed = 5 * 25; // the first chunk of 25 fails all 5 attempts
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    vi.useFakeTimers();

    const done = disconnect('conn-1');
    await vi.runAllTimersAsync();
    await expect(done).resolves.toMatchObject({ statusCode: 200 });

    expect(db.count('BatchWriteItemCommand')).toBe(5 + 2); // then 25 + 12 keys
    expect(rows()).toHaveLength(25);
    expect(warn).toHaveBeenCalledWith(
      '25 connection items not deleted, left to TTL',
    );
  });

  it('writes nothing for a connection it does not know', async () => {
    await disconnect('conn-unknown');
    expect(db.count('BatchWriteItemCommand')).toBe(0);
  });
});
