import { Duration, Stack, ArnFormat } from 'aws-cdk-lib';
import { PolicyStatement } from 'aws-cdk-lib/aws-iam';
import { Runtime, Architecture } from 'aws-cdk-lib/aws-lambda';
import { NodejsFunction } from 'aws-cdk-lib/aws-lambda-nodejs';
import { Table } from 'aws-cdk-lib/aws-dynamodb';
import { StringParameter } from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import * as path from 'path';
import { SSM_KEYS } from '../../constants/ssm-keys.js';

export class QaMcp extends Construct {
  public readonly function: NodejsFunction;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    const stack = Stack.of(this);

    const backendTableName = StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.BACKEND_TABLE_NAME,
    );
    const backendTable = Table.fromTableName(
      this,
      'BackendTable',
      backendTableName,
    );

    const qaRegeneratorFunctionArn = stack.formatArn({
      service: 'lambda',
      resource: 'function',
      resourceName: 'idp-v2-qa-regenerator',
      arnFormat: ArnFormat.COLON_RESOURCE_NAME,
    });

    this.function = new NodejsFunction(this, 'Function', {
      entry: path.resolve(
        process.cwd(),
        '../../packages/lambda/qa-mcp/src/handler.ts',
      ),
      handler: 'handler',
      runtime: Runtime.NODEJS_22_X,
      architecture: Architecture.ARM_64,
      timeout: Duration.minutes(2),
      memorySize: 256,
      environment: {
        BACKEND_TABLE_NAME: backendTableName,
        QA_REGENERATOR_FUNCTION_ARN: qaRegeneratorFunctionArn,
      },
    });

    backendTable.grantReadData(this.function);

    this.function.addToRolePolicy(
      new PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [qaRegeneratorFunctionArn],
      }),
    );
  }
}
