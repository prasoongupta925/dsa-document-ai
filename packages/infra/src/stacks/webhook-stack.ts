import { Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as path from 'path';
import { fileURLToPath } from 'url';
import {
  getRetentionDays,
  SSM_KEYS,
  toLogRetention,
} from ':idp-v2/common-constructs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Tests and caches stay out of the Lambda bundle
const ASSET_EXCLUDE = ['test_*.py', '__pycache__', '.pytest_cache', '*.pyc'];

/**
 * WebhookStack - pushes the signed loan-file verdict to a project's CRM
 * webhook (CallVarta CRM or any CRM).
 *
 * The delivery Lambda runs the file check (the file-check Lambda of McpStack)
 * and POSTs the verdict, signed with the project's secret
 * (X-DocAI-Signature, HMAC-SHA256). The workflow finalizer queues it
 * asynchronously after each document when the project's webhook is enabled;
 * the backend invokes it synchronously for POST .../integrations/webhook/test.
 *
 * It runs outside the VPC on purpose: the target URL is chosen by a user, so
 * the function must have no network path to private resources. The URL is
 * checked again at send time and every resolved address must be public.
 *
 * The signing secrets are stored encrypted with this stack's KMS key: the
 * backend may only Encrypt with it and this function is the only role allowed
 * to Decrypt, both only with the encryption context purpose
 * webhook-signing-secret (webhook_security.secret_encryption_context).
 */
export class WebhookStack extends Stack {
  public readonly deliveryFunction: lambda.Function;
  public readonly secretKey: kms.Key;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    const days = getRetentionDays(this);

    const backendTableName = ssm.StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.BACKEND_TABLE_NAME,
    );
    const backendTable = dynamodb.Table.fromTableName(
      this,
      'BackendTable',
      backendTableName,
    );

    // Deterministic loan-file check Lambda (McpStack, deployed before this stack)
    const fileCheckFunctionArn = ssm.StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.FILE_CHECK_MCP_FUNCTION_ARN,
    );

    // Encrypts each project's webhook signing secret (the stored ciphertext is
    // useless to every other role that can read the table).
    this.secretKey = new kms.Key(this, 'WebhookSecretKey', {
      description:
        'CRM webhook signing secrets: the backend encrypts, only the delivery Lambda decrypts',
      enableKeyRotation: true,
      removalPolicy: RemovalPolicy.DESTROY,
      pendingWindow: Duration.days(7),
    });

    this.deliveryFunction = new lambda.Function(this, 'WebhookDelivery', {
      functionName: 'idp-v2-webhook-delivery',
      description: 'Pushes the signed loan-file verdict to the project webhook',
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: 'index.handler',
      // File check (up to 30 s, looked at again while the document still
      // shows as pending) plus at most 25 s of delivery attempts.
      timeout: Duration.minutes(2),
      memorySize: 256,
      code: lambda.Code.fromAsset(
        path.join(__dirname, '../functions/webhook'),
        {
          exclude: ASSET_EXCLUDE,
        },
      ),
      environment: {
        BACKEND_TABLE_NAME: backendTableName,
        FILE_CHECK_FUNCTION_NAME: fileCheckFunctionArn,
        WEBHOOK_SECRET_KEY_ARN: this.secretKey.keyArn,
        // Delivery log items: expires_at = now + retentionDays (DynamoDB TTL)
        RETENTION_DAYS: String(days),
      },
      // The handler retries the POST itself and never raises; no Lambda
      // retries of asynchronous events, so no delivery is sent twice.
      retryAttempts: 0,
      logGroup: new logs.LogGroup(this, 'WebhookDeliveryLogs', {
        retention: toLogRetention(days),
        removalPolicy: RemovalPolicy.DESTROY,
      }),
    });

    // Read: the project META item (webhook settings), by key only (no Query
    // or Scan). Write: the delivery log items (PROJ#/WHDLV#). Both only in
    // project partitions (partition key PROJ#...).
    const projectPartitionsOnly = {
      'ForAllValues:StringLike': { 'dynamodb:LeadingKeys': ['PROJ#*'] },
    };
    this.deliveryFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ReadProjectWebhookSettings',
        actions: ['dynamodb:GetItem'],
        resources: [backendTable.tableArn],
        conditions: projectPartitionsOnly,
      }),
    );
    this.deliveryFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'WriteWebhookDeliveryLog',
        actions: ['dynamodb:PutItem'],
        resources: [backendTable.tableArn],
        conditions: projectPartitionsOnly,
      }),
    );

    // The only role that can read a signing secret
    this.deliveryFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'DecryptWebhookSecrets',
        actions: ['kms:Decrypt'],
        resources: [this.secretKey.keyArn],
        conditions: {
          StringEquals: {
            'kms:EncryptionContext:purpose': 'webhook-signing-secret',
          },
        },
      }),
    );

    // The file check it reports, and nothing else
    this.deliveryFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'InvokeFileCheck',
        actions: ['lambda:InvokeFunction'],
        resources: [fileCheckFunctionArn],
      }),
    );

    // The workflow finalizer (WorkflowStack) and the backend (ApplicationStack)
    // invoke it
    new ssm.StringParameter(this, 'WebhookFunctionArnParam', {
      parameterName: SSM_KEYS.WEBHOOK_FUNCTION_ARN,
      stringValue: this.deliveryFunction.functionArn,
      description: 'ARN of the CRM webhook delivery Lambda function',
    });
    // The backend (ApplicationStack) encrypts new signing secrets with it
    new ssm.StringParameter(this, 'WebhookSecretKeyArnParam', {
      parameterName: SSM_KEYS.WEBHOOK_SECRET_KEY_ARN,
      stringValue: this.secretKey.keyArn,
      description:
        'ARN of the KMS key that encrypts the CRM webhook signing secrets',
    });
  }
}
