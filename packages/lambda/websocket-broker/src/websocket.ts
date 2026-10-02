import {
  ApiGatewayManagementApiClient,
  PostToConnectionCommand,
  GoneException,
} from '@aws-sdk/client-apigatewaymanagementapi';
import { removeConnection } from './store.js';

const client = new ApiGatewayManagementApiClient({
  endpoint: process.env.WEBSOCKET_CALLBACK_URL,
});

export async function sendToConnection(
  connectionId: string,
  data: string,
): Promise<void> {
  try {
    await client.send(
      new PostToConnectionCommand({
        ConnectionId: connectionId,
        Data: data,
      }),
    );
  } catch (error) {
    if (error instanceof GoneException) {
      // Same cleanup as $disconnect, for connections whose disconnect event
      // never arrived (the table TTL is the last resort)
      console.log(`Connection ${connectionId} is gone, cleaning up`);
      await removeConnection(connectionId);
      return;
    }
    throw error;
  }
}
