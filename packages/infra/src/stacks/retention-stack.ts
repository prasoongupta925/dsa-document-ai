import { Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as triggers from 'aws-cdk-lib/triggers';
import * as path from 'path';
import { fileURLToPath } from 'url';
import {
  getRetentionDays,
  SSM_KEYS,
  toLogRetention,
} from ':idp-v2/common-constructs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Tests and caches stay out of the Lambda bundles
const ASSET_EXCLUDE = ['test_*.py', '__pycache__', '.pytest_cache', '*.pyc'];

/**
 * RetentionStack - nothing is kept longer than retentionDays (default 7)
 *
 * - RetentionSweeper (daily 02:00 IST): deletes documents older than the
 *   cutoff with the same cleanup as the backend's delete_document (S3,
 *   LanceDB, graph via SQS, DynamoDB), plus facts, datasets, chat sessions,
 *   artifacts and finished Amazon Transcribe jobs.
 * - LogRetentionEnforcer (daily 02:30 IST and on every deploy): caps every
 *   CloudWatch log group at retentionDays, including log groups that AWS
 *   services create on their own, and expires untagged ECR images.
 */
export class RetentionStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    const days = getRetentionDays(this);
    const logRetention = toLogRetention(days);

    // ========================================
    // Lookup Existing Resources (from SSM)
    // ========================================

    const backendTableName = ssm.StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.BACKEND_TABLE_NAME,
    );
    const backendTable = dynamodb.Table.fromTableName(
      this,
      'BackendTable',
      backendTableName,
    );

    const documentBucketName = ssm.StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.DOCUMENT_STORAGE_BUCKET_NAME,
    );
    const documentBucket = s3.Bucket.fromBucketName(
      this,
      'DocumentBucket',
      documentBucketName,
    );

    const sessionBucketName = ssm.StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.SESSION_STORAGE_BUCKET_NAME,
    );
    const sessionBucket = s3.Bucket.fromBucketName(
      this,
      'SessionBucket',
      sessionBucketName,
    );

    const agentBucketName = ssm.StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.AGENT_STORAGE_BUCKET_NAME,
    );
    const agentBucket = s3.Bucket.fromBucketName(
      this,
      'AgentBucket',
      agentBucketName,
    );

    const lanceServiceFunctionArn = ssm.StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.LANCE_SERVICE_FUNCTION_ARN,
    );

    const graphDeleteQueueUrl = ssm.StringParameter.valueForStringParameter(
      this,
      SSM_KEYS.GRAPH_DELETE_QUEUE_URL,
    );

    // ========================================
    // Retention Sweeper
    // ========================================

    const sweeper = new lambda.Function(this, 'RetentionSweeper', {
      functionName: 'idp-v2-retention-sweeper',
      description: `Deletes client data older than ${days} days`,
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: 'index.handler',
      timeout: Duration.minutes(15),
      memorySize: 512,
      code: lambda.Code.fromAsset(
        path.join(__dirname, '../functions/retention/sweeper'),
        { exclude: ASSET_EXCLUDE },
      ),
      environment: {
        BACKEND_TABLE_NAME: backendTableName,
        DOCUMENT_STORAGE_BUCKET_NAME: documentBucketName,
        SESSION_STORAGE_BUCKET_NAME: sessionBucketName,
        AGENT_STORAGE_BUCKET_NAME: agentBucketName,
        LANCEDB_FUNCTION_NAME: lanceServiceFunctionArn,
        GRAPH_DELETE_QUEUE_URL: graphDeleteQueueUrl,
        RETENTION_DAYS: String(days),
        DRY_RUN: 'false',
        DELETE_TRANSCRIBE_JOBS: 'true',
      },
      logGroup: new logs.LogGroup(this, 'RetentionSweeperLogs', {
        retention: logRetention,
        removalPolicy: RemovalPolicy.DESTROY,
      }),
    });

    // DynamoDB: Query/Scan/GetItem/DeleteItem/BatchWriteItem + GSI Query
    backendTable.grantReadWriteData(sweeper);
    sweeper.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['dynamodb:Query'],
        resources: [`${backendTable.tableArn}/index/*`],
      }),
    );

    // S3: list/get + delete on the client data buckets
    for (const bucket of [documentBucket, sessionBucket, agentBucket]) {
      bucket.grantRead(sweeper);
      bucket.grantDelete(sweeper);
    }

    // LanceDB service (delete_by_workflow, drop_table, graph keywords)
    sweeper.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [lanceServiceFunctionArn, `${lanceServiceFunctionArn}:*`],
      }),
    );

    // Graph delete is queued like the backend does (acked without work while
    // the graph is disabled)
    sweeper.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['sqs:SendMessage'],
        resources: [
          `arn:aws:sqs:${this.region}:${this.account}:idp-v2-graph-delete-queue`,
        ],
      }),
    );

    // Finished Amazon Transcribe jobs
    sweeper.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'transcribe:ListTranscriptionJobs',
          'transcribe:GetTranscriptionJob',
          'transcribe:DeleteTranscriptionJob',
        ],
        resources: ['*'],
      }),
    );

    // ========================================
    // Log Retention Enforcer
    // ========================================

    const enforcer = new lambda.Function(this, 'LogRetentionEnforcer', {
      functionName: 'idp-v2-log-retention-enforcer',
      description: `Caps CloudWatch log retention at ${days} days`,
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: 'index.handler',
      timeout: Duration.minutes(5),
      memorySize: 256,
      code: lambda.Code.fromAsset(
        path.join(__dirname, '../functions/retention/log-retention-enforcer'),
        { exclude: ASSET_EXCLUDE },
      ),
      environment: {
        RETENTION_DAYS: String(days),
        // Comma-separated log group prefixes; empty = every log group
        LOG_GROUP_PREFIXES: '',
        ECR_REPOSITORY_PREFIX: 'cdk-',
      },
      logGroup: new logs.LogGroup(this, 'LogRetentionEnforcerLogs', {
        retention: logRetention,
        removalPolicy: RemovalPolicy.DESTROY,
      }),
    });

    enforcer.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['logs:DescribeLogGroups'],
        resources: ['*'],
      }),
    );
    enforcer.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['logs:PutRetentionPolicy'],
        resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:*`],
      }),
    );
    enforcer.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ecr:DescribeRepositories'],
        resources: ['*'],
      }),
    );
    enforcer.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ecr:GetLifecyclePolicy', 'ecr:PutLifecyclePolicy'],
        resources: [
          `arn:aws:ecr:${this.region}:${this.account}:repository/cdk-*`,
        ],
      }),
    );

    // ========================================
    // Schedules (cron is UTC)
    // ========================================

    // 20:30 UTC = 02:00 IST
    new events.Rule(this, 'DailyRetentionSweep', {
      ruleName: 'idp-v2-daily-retention-sweep',
      description: `Delete client data older than ${days} days`,
      schedule: events.Schedule.cron({ minute: '30', hour: '20' }),
      targets: [new targets.LambdaFunction(sweeper)],
    });

    // 21:00 UTC = 02:30 IST
    new events.Rule(this, 'DailyLogRetention', {
      ruleName: 'idp-v2-daily-log-retention',
      description: `Cap CloudWatch log retention at ${days} days`,
      schedule: events.Schedule.cron({ minute: '0', hour: '21' }),
      targets: [new targets.LambdaFunction(enforcer)],
    });

    // Also run on deploy, so log groups created by this deploy are capped at
    // once. The handler never raises, so it cannot fail the deploy.
    new triggers.Trigger(this, 'EnforceLogRetentionOnDeploy', {
      handler: enforcer,
      executeOnHandlerChange: true,
      timeout: Duration.minutes(5),
    });
  }
}
