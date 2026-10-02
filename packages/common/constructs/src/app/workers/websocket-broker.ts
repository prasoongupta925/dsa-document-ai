import { Duration, Stack } from 'aws-cdk-lib';
import { ITable } from 'aws-cdk-lib/aws-dynamodb';
import { PolicyStatement } from 'aws-cdk-lib/aws-iam';
import { Runtime, Architecture } from 'aws-cdk-lib/aws-lambda';
import { NodejsFunction } from 'aws-cdk-lib/aws-lambda-nodejs';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import { IQueue } from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';
import * as path from 'path';

export interface WebsocketBrokerProps {
  /** WebSocket connection state (StorageStack, idp-v2-ws-connections) */
  connectionsTable: ITable;
  websocketCallbackUrl: string;
  websocketApiId: string;
  websocketMessageQueue: IQueue;
}

export class WebsocketBroker extends Construct {
  public readonly function: NodejsFunction;

  constructor(scope: Construct, id: string, props: WebsocketBrokerProps) {
    super(scope, id);

    this.function = new NodejsFunction(this, 'Function', {
      entry: path.resolve(
        process.cwd(),
        '../../packages/lambda/websocket-broker/src/index.ts',
      ),
      handler: 'handler',
      runtime: Runtime.NODEJS_22_X,
      architecture: Architecture.ARM_64,
      timeout: Duration.seconds(30),
      environment: {
        WS_CONNECTIONS_TABLE_NAME: props.connectionsTable.tableName,
        WEBSOCKET_CALLBACK_URL: props.websocketCallbackUrl,
      },
    });

    // Query/Scan the connections, delete the ones API Gateway reports gone
    props.connectionsTable.grantReadWriteData(this.function);

    const stack = Stack.of(this);
    this.function.addToRolePolicy(
      new PolicyStatement({
        actions: ['execute-api:ManageConnections'],
        resources: [
          `arn:aws:execute-api:${stack.region}:${stack.account}:${props.websocketApiId}/*/@connections/*`,
        ],
      }),
    );

    this.function.addEventSource(
      new SqsEventSource(props.websocketMessageQueue, {
        batchSize: 10,
      }),
    );
  }
}
