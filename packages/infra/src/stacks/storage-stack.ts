import { Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { StringParameter } from 'aws-cdk-lib/aws-ssm';
import {
  getRegionConfig,
  getRetentionDays,
  S3Bucket,
  S3DirectoryBucket,
  SSM_KEYS,
} from ':idp-v2/common-constructs';
import {
  AttributeType,
  Billing,
  StreamViewType,
  TableV2,
} from 'aws-cdk-lib/aws-dynamodb';
import { HttpMethods } from 'aws-cdk-lib/aws-s3';
import { Queue } from 'aws-cdk-lib/aws-sqs';

export class StorageStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // Client data (documents, derived analysis, chat sessions) is kept at
    // most retentionDays (CDK context, default 7).
    const retentionDays = getRetentionDays(this);
    const regionConfig = getRegionConfig(this);

    // LanceDB Lock Table
    const lancedbLockTable = new TableV2(this, 'LancedbLockTable', {
      partitionKey: { name: 'base_uri', type: AttributeType.STRING },
      sortKey: { name: 'version', type: AttributeType.NUMBER },
      billing: Billing.onDemand(),
    });

    new StringParameter(this, 'LancedbLockTableNameParam', {
      parameterName: SSM_KEYS.LANCEDB_LOCK_TABLE_NAME,
      stringValue: lancedbLockTable.tableName,
    });

    // Document Storage Bucket
    const documentStorage = new S3Bucket(this, 'DocumentStorage', {
      bucketPrefix: 'document-storage',
      bucketName: 'document-storage',
      expireObjectsAfterDays: retentionDays,
      cors: [
        {
          allowedOrigins: ['*'],
          allowedMethods: [
            HttpMethods.GET,
            HttpMethods.PUT,
            HttpMethods.POST,
            HttpMethods.HEAD,
          ],
          allowedHeaders: ['*'],
          exposedHeaders: [
            'ETag',
            'Content-Type',
            'Content-Length',
            'Accept-Ranges',
          ],
        },
      ],
    });

    new StringParameter(this, 'DocumentStorageBucketNameParam', {
      parameterName: SSM_KEYS.DOCUMENT_STORAGE_BUCKET_NAME,
      stringValue: documentStorage.bucket.bucketName,
    });

    // Session Storage Bucket (for agent conversation history)
    const sessionStorage = new S3Bucket(this, 'SessionStorage', {
      bucketPrefix: 'session-storage',
      bucketName: 'session-storage',
      cors: [
        {
          allowedOrigins: ['*'],
          allowedMethods: [HttpMethods.GET, HttpMethods.HEAD],
          allowedHeaders: ['*'],
          exposedHeaders: ['ETag', 'Content-Type', 'Content-Length'],
        },
      ],
      versioned: true,
      expireObjectsAfterDays: retentionDays,
    });

    new StringParameter(this, 'SessionStorageBucketNameParam', {
      parameterName: SSM_KEYS.SESSION_STORAGE_BUCKET_NAME,
      stringValue: sessionStorage.bucket.bucketName,
    });

    // Agent Storage Bucket (for custom agent prompts)
    // Structure: /{user_id}/{project_id}/agents/{agent_name}.md
    // No age-based expiry: this bucket also holds __prompts/ and custom-agent
    // config (app configuration). Chat artifacts
    // ({user_id}/{project_id}/artifacts/) are removed by the retention sweeper.
    const agentStorage = new S3Bucket(this, 'AgentStorage', {
      bucketPrefix: 'agent-storage',
      bucketName: 'agent-storage',
      cors: [
        {
          allowedOrigins: ['*'],
          allowedMethods: [HttpMethods.GET, HttpMethods.HEAD],
          allowedHeaders: ['*'],
          exposedHeaders: ['ETag', 'Content-Type', 'Content-Length'],
        },
      ],
    });

    new StringParameter(this, 'AgentStorageBucketNameParam', {
      parameterName: SSM_KEYS.AGENT_STORAGE_BUCKET_NAME,
      stringValue: agentStorage.bucket.bucketName,
    });

    // Model Artifacts Bucket (for ML models like PaddleOCR)
    // No age-based expiry: model files are app configuration, not client data.
    const modelArtifacts = new S3Bucket(this, 'ModelArtifacts', {
      bucketPrefix: 'model-artifacts',
      bucketName: 'model-artifacts',
    });

    new StringParameter(this, 'ModelArtifactsBucketNameParam', {
      parameterName: SSM_KEYS.MODEL_ARTIFACTS_BUCKET_NAME,
      stringValue: modelArtifacts.bucket.bucketName,
    });

    // Backend Table (One Table Design)
    // TTL on expires_at (epoch seconds): items that set it delete themselves
    // (today the file-check Ask usage ledger, PROJ#/FCASK#, and the CRM webhook
    // delivery log, PROJ#/WHDLV#: now + retentionDays).
    // Items without the attribute are never expired. Enabling TTL is an
    // in-place update; its REMOVE stream records match no WorkflowStream filter.
    const backendTable = new TableV2(this, 'BackendTable', {
      partitionKey: { name: 'PK', type: AttributeType.STRING },
      sortKey: { name: 'SK', type: AttributeType.STRING },
      billing: Billing.onDemand(),
      timeToLiveAttribute: 'expires_at',
      dynamoStream: StreamViewType.NEW_AND_OLD_IMAGES,
      globalSecondaryIndexes: [
        {
          indexName: 'GSI1',
          partitionKey: { name: 'GSI1PK', type: AttributeType.STRING },
          sortKey: { name: 'GSI1SK', type: AttributeType.STRING },
        },
        {
          indexName: 'GSI2',
          partitionKey: { name: 'GSI2PK', type: AttributeType.STRING },
          sortKey: { name: 'GSI2SK', type: AttributeType.STRING },
        },
      ],
    });

    new StringParameter(this, 'BackendTableNameParam', {
      parameterName: SSM_KEYS.BACKEND_TABLE_NAME,
      stringValue: backendTable.tableName,
    });

    new StringParameter(this, 'BackendTableStreamArnParam', {
      parameterName: SSM_KEYS.BACKEND_TABLE_STREAM_ARN,
      stringValue: backendTable.tableStreamArn!,
    });

    // Express One Zone Storage Bucket
    // No age-based lifecycle: LanceDB data is deleted through LanceDB
    // (delete_by_workflow / drop_table) so table manifests stay consistent.
    const expressStorage = new S3DirectoryBucket(this, 'ExpressStorage', {
      bucketPrefix: 'lancedb-ex',
      availabilityZoneId: regionConfig.lancedbExpressAzId,
    });

    new StringParameter(this, 'LancedbExpressBucketNameParam', {
      parameterName: SSM_KEYS.LANCEDB_EXPRESS_BUCKET_NAME,
      stringValue: expressStorage.bucketName,
    });

    new StringParameter(this, 'LancedbExpressAzIdParam', {
      parameterName: SSM_KEYS.LANCEDB_EXPRESS_AZ_ID,
      stringValue: regionConfig.lancedbExpressAzId,
    });

    // WebSocket connection state (replaces ElastiCache Serverless / Valkey):
    // which user each connection belongs to and which projects it follows.
    // On demand, so nothing to pay while idle, and no VPC for its Lambdas.
    // Every item carries expires_at = now + 24 h (TTL); API Gateway closes a
    // connection after 2 h, so only leftovers of a lost $disconnect expire.
    // Layout: packages/lambda/websocket/src/keys.ts.
    const wsConnectionsTable = new TableV2(this, 'WsConnectionsTable', {
      tableName: 'idp-v2-ws-connections',
      partitionKey: { name: 'pk', type: AttributeType.STRING },
      sortKey: { name: 'sk', type: AttributeType.STRING },
      billing: Billing.onDemand(),
      timeToLiveAttribute: 'expires_at',
      removalPolicy: RemovalPolicy.DESTROY,
    });

    new StringParameter(this, 'WsConnectionsTableNameParam', {
      parameterName: SSM_KEYS.WS_CONNECTIONS_TABLE_NAME,
      stringValue: wsConnectionsTable.tableName,
    });

    // WebSocket Message Queue (SQS allows at most 14 days)
    const queueRetention = Duration.days(Math.min(retentionDays, 14));
    const websocketMessageDlq = new Queue(this, 'WebsocketMessageDLQ', {
      queueName: 'idp-v2-websocket-message-dlq',
      retentionPeriod: queueRetention,
    });

    const websocketMessageQueue = new Queue(this, 'WebsocketMessageQueue', {
      queueName: 'idp-v2-websocket-message-queue',
      visibilityTimeout: Duration.minutes(5),
      retentionPeriod: queueRetention,
      deadLetterQueue: {
        queue: websocketMessageDlq,
        maxReceiveCount: 3,
      },
    });

    new StringParameter(this, 'WebsocketMessageQueueArnParam', {
      parameterName: SSM_KEYS.WEBSOCKET_MESSAGE_QUEUE_ARN,
      stringValue: websocketMessageQueue.queueArn,
    });
  }
}
