import { ApplicationStack } from './stacks/application-stack.js';
import { AgentStack } from './stacks/agent-stack.js';
import { McpStack } from './stacks/mcp-stack.js';
import { App } from ':idp-v2/common-constructs';
import { StorageStack } from './stacks/storage-stack.js';
import { EventStack } from './stacks/event-stack.js';
import { BdaStack } from './stacks/bda-stack.js';
import { OcrStack } from './stacks/ocr-stack.js';
import { TranscribeStack } from './stacks/transcribe-stack.js';
import { WorkflowStack } from './stacks/workflow-stack.js';
import { WorkerStack } from './stacks/worker-stack.js';
import { WebcrawlerStack } from './stacks/webcrawler-stack.js';
import { WebsocketStack } from './stacks/websocket-stack.js';
import { LanceServiceStack } from './stacks/lance-service-stack.js';
import { RetentionStack } from './stacks/retention-stack.js';
import { WebhookStack } from './stacks/webhook-stack.js';

const app = new App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

// ============================================================
// [With Dependencies] - uncomment this block for production
// ============================================================
const storageStack = new StorageStack(app, 'IDP-V2-Storage', { env });

const eventStack = new EventStack(app, 'IDP-V2-Event', { env });
eventStack.addDependency(storageStack);

const ocrStack = new OcrStack(app, 'IDP-V2-Ocr', { env });
ocrStack.addDependency(storageStack);
ocrStack.addDependency(eventStack);

const bdaStack = new BdaStack(app, 'IDP-V2-Bda', { env });
bdaStack.addDependency(eventStack);

const transcribeStack = new TranscribeStack(app, 'IDP-V2-Transcribe', { env });
transcribeStack.addDependency(eventStack);

const websocketStack = new WebsocketStack(app, 'IDP-V2-Websocket', { env });
websocketStack.addDependency(storageStack);

const mcpStack = new McpStack(app, 'IDP-V2-Mcp', { env });
mcpStack.addDependency(storageStack);
mcpStack.addDependency(websocketStack);

const workerStack = new WorkerStack(app, 'IDP-V2-Worker', { env });
workerStack.addDependency(storageStack);
workerStack.addDependency(websocketStack);

const agentStack = new AgentStack(app, 'IDP-V2-Agent', {
  env,
  gateway: mcpStack.gateway,
});
agentStack.addDependency(storageStack);
agentStack.addDependency(mcpStack);

const webcrawlerStack = new WebcrawlerStack(app, 'IDP-V2-Webcrawler', {
  env,
});
webcrawlerStack.addDependency(eventStack);
webcrawlerStack.addDependency(agentStack);

const lanceServiceStack = new LanceServiceStack(app, 'IDP-V2-LanceService', {
  env,
});
lanceServiceStack.addDependency(storageStack);
// Mcp reads the LanceDB service ARN from SSM at deploy time.
mcpStack.addDependency(lanceServiceStack);

// CRM webhook delivery (signed loan-file verdict push). Reads the backend table
// (Storage) and invokes the file-check Lambda (Mcp); the workflow finalizer and
// the backend read its ARN from SSM, so both stacks deploy after it.
const webhookStack = new WebhookStack(app, 'IDP-V2-Webhook', { env });
webhookStack.addDependency(storageStack);
webhookStack.addDependency(mcpStack);

const workflowStack = new WorkflowStack(app, 'IDP-V2-Workflow', { env });
workflowStack.addDependency(storageStack);
workflowStack.addDependency(eventStack);
workflowStack.addDependency(ocrStack);
workflowStack.addDependency(webcrawlerStack);
workflowStack.addDependency(agentStack);
workflowStack.addDependency(lanceServiceStack);
workflowStack.addDependency(webhookStack);

const applicationStack = new ApplicationStack(app, 'IDP-V2-Application', {
  env,
  crossRegionReferences: true,
});
applicationStack.addDependency(storageStack);
applicationStack.addDependency(agentStack);
applicationStack.addDependency(websocketStack);
applicationStack.addDependency(mcpStack);
applicationStack.addDependency(workflowStack);
applicationStack.addDependency(webhookStack);

// Retention (daily sweeper + log retention enforcer). Deployed last so every
// resource it cleans up, and every log group it caps, already exists.
const retentionStack = new RetentionStack(app, 'IDP-V2-Retention', { env });
retentionStack.addDependency(storageStack);
retentionStack.addDependency(workflowStack);
retentionStack.addDependency(agentStack);
retentionStack.addDependency(applicationStack);
retentionStack.addDependency(webhookStack);

// ============================================================
// [Without Dependencies] - for independent stack deployment (dev)
// ============================================================
// new StorageStack(app, 'IDP-V2-Storage', { env });
// new EventStack(app, 'IDP-V2-Event', { env });
// new OcrStack(app, 'IDP-V2-Ocr', { env });
// new BdaStack(app, 'IDP-V2-Bda', { env });
// new TranscribeStack(app, 'IDP-V2-Transcribe', { env });
// new WorkflowStack(app, 'IDP-V2-Workflow', { env });
// new WebsocketStack(app, 'IDP-V2-Websocket', { env });
// const mcpStack = new McpStack(app, 'IDP-V2-Mcp', { env });
// new WorkerStack(app, 'IDP-V2-Worker', { env });
// new AgentStack(app, 'IDP-V2-Agent', {
//   env,
//   gateway: mcpStack.gateway,
// });
// new LanceServiceStack(app, 'IDP-V2-LanceService', { env });
// new WebhookStack(app, 'IDP-V2-Webhook', { env });
// new WebcrawlerStack(app, 'IDP-V2-Webcrawler', { env });
// new ApplicationStack(app, 'IDP-V2-Application', {
//   env,
//   crossRegionReferences: true,
// });
// new RetentionStack(app, 'IDP-V2-Retention', { env });

app.synth();
