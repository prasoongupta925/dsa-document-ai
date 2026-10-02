import type { SQSHandler } from 'aws-lambda';
import { getAllConnectionIds, getConnectionIdsByUsername } from './store.js';
import { sendToConnection } from './websocket.js';

export const handler: SQSHandler = async (event) => {
  for (const record of event.Records) {
    const { username, message } = JSON.parse(record.body);

    // No username: broadcast to every open connection
    const connectionIds = username
      ? await getConnectionIdsByUsername(username)
      : await getAllConnectionIds();

    await Promise.all(
      connectionIds.map((id) => sendToConnection(id, JSON.stringify(message))),
    );
  }
};
