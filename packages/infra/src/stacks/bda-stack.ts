import { Stack, StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';

/**
 * BdaStack - Bedrock Document Analysis (placeholder)
 *
 * BDA preprocessing is now orchestrated by WorkflowStack Step Functions.
 * This stack is kept for backward compatibility and future BDA-specific resources.
 */
export class BdaStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);
  }
}
