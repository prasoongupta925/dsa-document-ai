import {
  ApiGatewayManagementApiClient,
  GoneException,
  type PostToConnectionCommand,
} from '@aws-sdk/client-apigatewaymanagementapi';
import {
  BatchWriteItemCommand,
  DynamoDBClient,
} from '@aws-sdk/client-dynamodb';
import type { Context, DynamoDBRecord, DynamoDBStreamEvent } from 'aws-lambda';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { handler } from './index.js';
import { FakeDynamoDB } from './test/fake-dynamodb.js';

const WS_TABLE = 'idp-v2-ws-connections';

let db: FakeDynamoDB;
let posted: { connectionId: string; message: unknown }[];
let gone: Set<string>;

/** A connection as the websocket Lambdas write it. */
function seedConnection(
  connectionId: string,
  username: string,
  projectIds: string[],
) {
  db.seed(WS_TABLE, [
    {
      pk: { S: `CONN#${connectionId}` },
      sk: { S: 'META' },
      userSub: { S: `sub-${username}` },
      username: { S: username },
    },
    { pk: { S: `USER#${username}` }, sk: { S: `CONN#${connectionId}` } },
    ...projectIds.flatMap((projectId) => [
      { pk: { S: `CONN#${connectionId}` }, sk: { S: `PROJ#${projectId}` } },
      { pk: { S: `PROJ#${projectId}` }, sk: { S: `CONN#${connectionId}` } },
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
      posted.push({ connectionId, message: JSON.parse(String(input.Data)) });
      return {};
    },
  );
  vi.spyOn(console, 'log').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function run(...Records: unknown[]) {
  const event = { Records } as DynamoDBStreamEvent;
  return handler(event, {} as Context, () => undefined);
}

function workflowStatusChange(from: string, to: string): DynamoDBRecord {
  const image = (status: string) => ({
    PK: { S: 'DOC#doc-1' },
    SK: { S: 'WF#wf-1' },
    data: { M: { status: { S: status }, project_id: { S: 'proj-1' } } },
  });
  return {
    eventName: 'MODIFY',
    dynamodb: { OldImage: image(from), NewImage: image(to) },
  };
}

/** The connections table as [pk, sk] pairs. */
const rows = () => db.items(WS_TABLE).map((item) => [item.pk.S, item.sk.S]);

describe('workflow-stream', () => {
  it("sends a status change to the project's subscribers only", async () => {
    seedConnection('conn-1', 'alice', ['proj-1']);
    seedConnection('conn-2', 'bob', ['proj-1', 'proj-2']);
    seedConnection('conn-3', 'carol', ['proj-2']);
    seedConnection('conn-4', 'dave', ['proj-1']);

    await run(workflowStatusChange('in_progress', 'completed'));

    expect(posted.map((p) => p.connectionId).sort()).toEqual([
      'conn-1',
      'conn-2',
      'conn-4',
    ]);
    expect(posted[0].message).toMatchObject({
      action: 'workflow',
      data: {
        event: 'status_changed',
        workflowId: 'wf-1',
        documentId: 'doc-1',
        projectId: 'proj-1',
        status: 'completed',
        previousStatus: 'in_progress',
      },
    });
  });

  it('sends a document deletion to the subscribers', async () => {
    seedConnection('conn-1', 'alice', ['proj-1']);

    await run({
      eventName: 'REMOVE',
      dynamodb: {
        OldImage: { PK: { S: 'PROJ#proj-1' }, SK: { S: 'DOC#doc-1' } },
      },
    });

    expect(posted).toEqual([
      {
        connectionId: 'conn-1',
        message: expect.objectContaining({
          action: 'document',
          data: expect.objectContaining({
            event: 'deleted',
            documentId: 'doc-1',
            projectId: 'proj-1',
          }),
        }),
      },
    ]);
  });

  it('removes every trace of a gone connection', async () => {
    seedConnection('conn-1', 'alice', ['proj-1', 'proj-2']);
    seedConnection('conn-2', 'alice', ['proj-1']);
    gone.add('conn-1');

    await run(workflowStatusChange('in_progress', 'failed'));

    expect(posted.map((p) => p.connectionId)).toEqual(['conn-2']);
    expect(rows()).toEqual([
      ['CONN#conn-2', 'META'],
      ['CONN#conn-2', 'PROJ#proj-1'],
      ['PROJ#proj-1', 'CONN#conn-2'],
      ['USER#alice', 'CONN#conn-2'],
    ]);
  });

  it('only logs a failed cleanup', async () => {
    seedConnection('conn-1', 'alice', ['proj-1']);
    gone.add('conn-1');
    const error = vi
      .spyOn(console, 'error')
      .mockImplementation(() => undefined);
    const send = vi.mocked(DynamoDBClient.prototype.send);
    send.mockImplementation((command) =>
      command instanceof BatchWriteItemCommand
        ? Promise.reject(new Error('throttled'))
        : db.send(command),
    );

    await run(workflowStatusChange('in_progress', 'completed'));

    expect(error).toHaveBeenCalledWith(
      'Failed to remove stale connection conn-1:',
      expect.any(Error),
    );
    expect(rows()).toHaveLength(4);
  });

  it('skips a project nobody follows', async () => {
    seedConnection('conn-1', 'alice', ['proj-2']);

    await run(workflowStatusChange('in_progress', 'completed'));

    expect(posted).toEqual([]);
  });
});
