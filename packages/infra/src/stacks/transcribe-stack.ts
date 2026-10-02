import { Stack, StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';

/**
 * TranscribeStack - Video/Audio Transcription (placeholder)
 *
 * Transcription is now orchestrated by WorkflowStack Step Functions.
 * This stack is kept for backward compatibility and future Transcribe-specific resources.
 */
export class TranscribeStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);
  }
}
