import { Stack, StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';

/**
 * WebcrawlerStack - WebCrawler (placeholder)
 *
 * WebCrawler invocation is now orchestrated by WorkflowStack Step Functions.
 * This stack is kept for backward compatibility and future WebCrawler-specific resources.
 */
export class WebcrawlerStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);
  }
}
