import { Duration } from 'aws-cdk-lib';
import { ITable, Table } from 'aws-cdk-lib/aws-dynamodb';
import { Runtime, Architecture } from 'aws-cdk-lib/aws-lambda';
import { NodejsFunction } from 'aws-cdk-lib/aws-lambda-nodejs';
import { Construct } from 'constructs';
import * as path from 'path';

export interface WebsocketFunctionsProps {
  backendTableName: string;
  /** WebSocket connection state (StorageStack, idp-v2-ws-connections) */
  connectionsTable: ITable;
}

export class WebsocketFunctions extends Construct {
  public readonly connectFunction: NodejsFunction;
  public readonly defaultFunction: NodejsFunction;
  public readonly disconnectFunction: NodejsFunction;

  constructor(scope: Construct, id: string, props: WebsocketFunctionsProps) {
    super(scope, id);

    const { backendTableName, connectionsTable } = props;

    const backendTable = Table.fromTableName(
      this,
      'BackendTable',
      backendTableName,
    );

    this.connectFunction = new NodejsFunction(this, 'ConnectFunction', {
      entry: path.resolve(
        process.cwd(),
        '../../packages/lambda/websocket/src/connect.ts',
      ),
      handler: 'connectHandler',
      runtime: Runtime.NODEJS_22_X,
      architecture: Architecture.ARM_64,
      timeout: Duration.seconds(2),
      environment: {
        WS_CONNECTIONS_TABLE_NAME: connectionsTable.tableName,
        BACKEND_TABLE_NAME: backendTableName,
      },
    });

    backendTable.grantReadData(this.connectFunction);
    connectionsTable.grantWriteData(this.connectFunction);

    this.defaultFunction = new NodejsFunction(this, 'DefaultFunction', {
      entry: path.resolve(
        process.cwd(),
        '../../packages/lambda/websocket/src/default.ts',
      ),
      handler: 'defaultHandler',
      runtime: Runtime.NODEJS_22_X,
      architecture: Architecture.ARM_64,
      timeout: Duration.seconds(2),
      environment: {
        WS_CONNECTIONS_TABLE_NAME: connectionsTable.tableName,
      },
    });

    connectionsTable.grantWriteData(this.defaultFunction);

    this.disconnectFunction = new NodejsFunction(this, 'DisconnectFunction', {
      entry: path.resolve(
        process.cwd(),
        '../../packages/lambda/websocket/src/disconnect.ts',
      ),
      handler: 'disconnectHandler',
      runtime: Runtime.NODEJS_22_X,
      architecture: Architecture.ARM_64,
      timeout: Duration.seconds(10),
      environment: {
        WS_CONNECTIONS_TABLE_NAME: connectionsTable.tableName,
      },
    });

    connectionsTable.grantReadWriteData(this.disconnectFunction);
  }
}
