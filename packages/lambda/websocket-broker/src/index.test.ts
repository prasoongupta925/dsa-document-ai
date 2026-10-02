import {
  ApiGatewayManagementApiClient,
  GoneException,
  type PostToConnectionCommand,
} from '@aws-sdk/client-apigatewaymanagementapi';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import type { Context, SQSEvent } from 'aws-lambda';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { handler } from './index.js';
import { FakeDynamoDB } from './test/fake-dynamodb.js';

const WS_TABLE = 'idp-v2-ws-connections';
const MESSAGE = {
  action: 'sessions',
  data: { event: 'created', sessionId: 's-1', sessionName: 'New chat' },
};

let db: FakeDynamoDB;
let posted: string[];
let gone: Set<string>;

/** A connection as the websocket Lambdas write it. */
function seedConnection(
  connectionId: string,
  username: string,
  projectIds: string[] = [],
) {
  const ttl = { expires_at: { N: '1790856000' } };
  db.seed(WS_TABLE, [
    {
      pk: { S: `CONN#${connectionId}` },
      sk: { S: 'META' },
      userSub: { S: `sub-${username}` },
      username: { S: username },
      ...ttl,
    },
    {
      pk: { S: `USER#${username}` },
      sk: { S: `CONN#${connectionId}` },
      ...ttl,
    },
    ...projectIds.flatMap((projectId) => [
      {
        pk: { S: `CONN#${connectionId}` },
        sk: { S: `PROJ#${projectId}` },
        ...ttl,
      },
      {
        pk: { S: `PROJ#${projectId}` },
        sk: { S: `CONN#${connectionId}` },
        ...ttl,
      },
    ]),
  ]);
}

beforeEach(() => {
  process.env.WS_CONNECTIONS_TABLE_NAME = WS_TABLE;
  db = new FakeDynamoDB({ [WS_TABLE]: ['pk', 'sk'] });
  posted = [];
  gone = new Set();
  vi.spyOn(DynamoDBClient.prototype, 'send').mockImplementation((command) =>
    db.send(command),
  );
  vi.spyOn(ApiGatewayManagementApiClient.prototype, 'send').mockImplementation(
    async (command) => {
      const { input } = command as PostToConnectionCommand;
      const connectionId = String(input.ConnectionId);
      if (gone.has(connectionId)) {
        throw new GoneException({ message: 'Gone', $metadata: {} });
      }
      expect(input.Data).toBe(JSON.stringify(MESSAGE));
      posted.push(connectionId);
      return {};
    },
  );
  vi.spyOn(console, 'log').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function run(...bodies: unknown[]) {
  const event = {
    Records: bodies.map((body, i) => ({
      messageId: `m-${i}`,
      body: JSON.stringify(body),
    })),
  } as unknown as SQSEvent;
  return handler(event, {} as Context, () => undefined);
}

/** The connections table as [pk, sk] pairs. */
const rows = () => db.items(WS_TABLE).map((item) => [item.pk.S, item.sk.S]);

describe('websocket-broker', () => {
  it("sends a user's message to that user's connections only", async () => {
    seedConnection('conn-1', 'alice', ['proj-1']);
    seedConnection('conn-2', 'alice');
    seedConnection('conn-3', 'alice');
    seedConnection('conn-4', 'bob', ['proj-1']);

    await run({ username: 'alice', message: MESSAGE, projectId: 'proj-1' });

    expect(posted.sort()).toEqual(['conn-1', 'conn-2', 'conn-3']);
    // a session list is no longer cached anywhere: reads only, nothing deleted
    expect(new Set(db.calls.map((call) => call.command))).toEqual(
      new Set(['QueryCommand']),
    );
  });

  it('broadcasts to every open connection when there is no username', async () => {
    seedConnection('conn-1', 'alice', ['proj-1', 'proj-2']);
    seedConnection('conn-2', 'bob', ['proj-1']);
    seedConnection('conn-3', 'carol');

    await run({ username: null, message: MESSAGE });

    expect(posted.sort()).toEqual(['conn-1', 'conn-2', 'conn-3']);
    expect(db.count('ScanCommand')).toBeGreaterThan(1); // followed every page
  });

  it('removes every trace of a gone connection', async () => {
    seedConnection('conn-1', 'alice', ['proj-1']);
    seedConnection('conn-2', 'alice', ['proj-1']);
    gone.add('conn-1');

    await run({ username: 'alice', message: MESSAGE });

    expect(posted).toEqual(['conn-2']);
    expect(rows()).toEqual([
      ['CONN#conn-2', 'META'],
      ['CONN#conn-2', 'PROJ#proj-1'],
      ['PROJ#proj-1', 'CONN#conn-2'],
      ['USER#alice', 'CONN#conn-2'],
    ]);
  });

  it('fails the batch on other API Gateway errors so SQS retries', async () => {
    seedConnection('conn-1', 'alice');
    vi.mocked(ApiGatewayManagementApiClient.prototype.send).mockRejectedValue(
      new Error('throttled'),
    );

    await expect(run({ username: 'alice', message: MESSAGE })).rejects.toThrow(
      'throttled',
    );
    expect(rows()).toHaveLength(2);
  });

  it('does nothing for a user without connections', async () => {
    seedConnection('conn-1', 'alice');

    await run({ username: 'bob', message: MESSAGE });

    expect(posted).toEqual([]);
    expect(rows()).toHaveLength(2);
  });
});
