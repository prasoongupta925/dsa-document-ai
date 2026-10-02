import { GetItemCommand } from '@aws-sdk/client-dynamodb';
import type { APIGatewayProxyHandler } from 'aws-lambda';
import { addConnection, ddb } from './store.js';

// One GetItem on the backend table. The old Valkey copy of this mapping is
// gone: a DynamoDB cache in front of a DynamoDB read only adds requests.
async function getUsernameFromSub(
  userSub: string,
): Promise<string | undefined> {
  const { Item } = await ddb.send(
    new GetItemCommand({
      TableName: process.env.BACKEND_TABLE_NAME,
      Key: { PK: { S: `USERSUB#${userSub}` }, SK: { S: 'META' } },
    }),
  );

  return Item?.data?.M?.username?.S;
}

export const connectHandler: APIGatewayProxyHandler = async (event) => {
  const { connectionId, identity } = event.requestContext;

  const userSub = identity?.cognitoAuthenticationProvider?.split(':').pop();

  if (connectionId && userSub) {
    const username = await getUsernameFromSub(userSub);

    if (username) {
      await addConnection(connectionId, userSub, username);
    }
  }

  return { statusCode: 200, body: 'Connected' };
};
