import { Aws, Duration, RemovalPolicy } from 'aws-cdk-lib';
import { Bucket, CorsRule, IBucket, LifecycleRule } from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { getRetentionDays } from './retention-config.js';

export interface S3BucketProps {
  readonly bucketPrefix: string;
  readonly cors?: CorsRule[];
  readonly versioned?: boolean;
  /**
   * Explicit bucket name prefix. Final name:
   * `idp-v2-{bucketName}-{account}-{region}`.
   * If omitted, CDK auto-generates the name.
   */
  readonly bucketName?: string;
  /**
   * Expire CURRENT objects after N days (client data buckets only).
   * If omitted, current objects never expire.
   */
  readonly expireObjectsAfterDays?: number;
}

export class S3Bucket extends Construct {
  public readonly bucket: IBucket;
  public readonly logBucket: IBucket;

  constructor(scope: Construct, id: string, props: S3BucketProps) {
    super(scope, id);

    const { bucketPrefix, expireObjectsAfterDays } = props;
    // Bucket names are global: include the region so a deploy in another
    // region (or right after a teardown elsewhere) cannot collide.
    const baseName = props.bucketName
      ? `idp-v2-${props.bucketName}-${Aws.ACCOUNT_ID}-${Aws.REGION}`
      : undefined;

    this.logBucket = new Bucket(this, `${bucketPrefix}-LogBucket`, {
      bucketName: baseName ? `${baseName}-logs` : undefined,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      enforceSSL: true,
      lifecycleRules: [
        {
          id: 'expire-logs',
          expiration: Duration.days(getRetentionDays(this)),
          abortIncompleteMultipartUploadAfter: Duration.days(1),
        },
      ],
    });

    const lifecycleRules: LifecycleRule[] = [
      {
        id: 'abort-incomplete-mpu',
        abortIncompleteMultipartUploadAfter: Duration.days(1),
      },
      {
        id: 'noncurrent-1d',
        noncurrentVersionExpiration: Duration.days(1),
        expiredObjectDeleteMarker: true,
      },
    ];
    if (expireObjectsAfterDays !== undefined) {
      lifecycleRules.push({
        id: 'expire-current',
        expiration: Duration.days(expireObjectsAfterDays),
      });
    }

    this.bucket = new Bucket(this, `${bucketPrefix}-Bucket`, {
      bucketName: baseName ?? undefined,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      enforceSSL: true,
      serverAccessLogsBucket: this.logBucket,
      serverAccessLogsPrefix: 'access-logs/',
      cors: props.cors,
      versioned: props.versioned,
      lifecycleRules,
    });
  }
}
