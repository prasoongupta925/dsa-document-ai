import type { APIGatewayProxyHandler } from 'aws-lambda';
import { removeConnection } from './store.js';

export const disconnectHandler: APIGatewayProxyHandler = async (event) => {
  const { connectionId } = event.requestContext;

  if (connectionId) {
    // Clean up the user connection and its project subscriptions
    await removeConnection(connectionId);
  }

  console.log('WebSocket disconnected', { connectionId });

  return { statusCode: 200, body: 'Disconnected' };
};
