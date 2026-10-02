import { ArnFormat, Duration, Stack } from 'aws-cdk-lib';
import { Effect, PolicyStatement } from 'aws-cdk-lib/aws-iam';
import { Runtime, Architecture } from 'aws-cdk-lib/aws-lambda';
import { NodejsFunction } from 'aws-cdk-lib/aws-lambda-nodejs';
import { Bucket } from 'aws-cdk-lib/aws-s3';
import { StringParameter } from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import * as path from 'path';
import { SSM_KEYS } from '../../constants/ssm-keys.js';
import { BLOCKED_BEDROCK_MODEL_RESOURCES } from '../../constants/bedrock.js';
import { getRegionConfig } from '../../core/region-config.js';

export class SearchMcp extends Construct {
  public readonly function: NodejsFunction;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    const documentStorageBucketName = StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.DOCUMENT_STORAGE_BUCKET_NAME,
    );
    const documentStorageBucket = Bucket.fromBucketName(
      this,
      'DocumentStorageBucket',
      documentStorageBucketName,
    );

    const lancedbFunctionArn = StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.LANCE_SERVICE_FUNCTION_ARN,
    );

    const graphServiceFunctionArn = Stack.of(this).formatArn({
      service: 'lambda',
      resource: 'function',
      resourceName: 'idp-v2-graph-service',
      arnFormat: ArnFormat.COLON_RESOURCE_NAME,
    });

    this.function = new NodejsFunction(this, 'Function', {
      entry: path.resolve(
        process.cwd(),
        '../../packages/lambda/search-mcp/src/handler.ts',
      ),
      handler: 'handler',
      runtime: Runtime.NODEJS_22_X,
      architecture: Architecture.ARM_64,
      timeout: Duration.minutes(5),
      memorySize: 256,
      environment: {
        LANCEDB_FUNCTION_ARN: lancedbFunctionArn,
        GRAPH_SERVICE_FUNCTION_ARN: graphServiceFunctionArn,
        DOCUMENT_STORAGE_BUCKET: documentStorageBucketName,
        SUMMARIZE_MODEL_ID: 'global.amazon.nova-2-lite-v1:0',
        RERANK_MODEL_ID: 'amazon.rerank-v1:0',
        // Amazon Rerank is not offered in every region (e.g. ap-south-1);
        // region is config (context rerankRegion)
        RERANK_REGION: getRegionConfig(this).rerankRegion,
      },
    });

    documentStorageBucket.grantRead(this.function);

    this.function.addToRolePolicy(
      new PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [lancedbFunctionArn, graphServiceFunctionArn],
      }),
    );

    this.function.addToRolePolicy(
      new PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:Rerank'],
        resources: ['*'],
      }),
    );

    // Only AWS-sold models: explicitly deny non-AWS-sold model providers.
    this.function.addToRolePolicy(
      new PolicyStatement({
        sid: 'DenyNonAwsSoldModels',
        effect: Effect.DENY,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: BLOCKED_BEDROCK_MODEL_RESOURCES,
      }),
    );

    new StringParameter(this, 'FunctionArnParam', {
      parameterName: SSM_KEYS.SEARCH_MCP_FUNCTION_ARN,
      stringValue: this.function.functionArn,
      description: 'ARN of the Search MCP Lambda function',
    });

    if (this.function.role) {
      new StringParameter(this, 'RoleArnParam', {
        parameterName: SSM_KEYS.SEARCH_MCP_ROLE_ARN,
        stringValue: this.function.role.roleArn,
        description: 'ARN of the Search MCP Lambda role',
      });
    }
  }
}
