import { TEAM_COLLABORATION_ENABLED } from "./agent-visibility";
import type { RunActivity } from "./activity-schema";
import { requireAuthenticatedResponse } from "./client-auth";
import { createRandomId } from "./random-id";
import { streamAgui } from "./agui";
import type {
  BuiltinToolOption,
  McpOption,
  ModelRouteOption,
  StudioDraft,
  StudioEvalCase,
  StudioRisk,
  StudioSkill,
} from "./agent-studio";

export type StudioRole = "owner" | "admin" | "member" | "viewer";

export type LifecycleScope = {
  kind: "tenant" | "user" | "session" | "agent";
  subjectId: string;
};

export type RetentionPolicy = {
  tenantId: string;
  policyId: string;
  revision: number;
  sessionDays: number;
  artifactDays: number;
  traceDays: number;
  evalDays: number;
  updatedBy: string;
  updatedAt: string;
};

export type LegalHold = {
  tenantId: string;
  holdId: string;
  scope: LifecycleScope;
  reason: string;
  active: boolean;
  createdBy: string;
  createdAt: string;
  releasedBy: string | null;
  releasedAt: string | null;
};

export type DataLifecycleJob = {
  tenantId: string;
  jobId: string;
  kind: "export" | "delete" | "retention";
  scope: LifecycleScope;
  requestedBy: string;
  idempotencyKey: string;
  status: "queued" | "running" | "succeeded" | "partial_failed" | "failed";
  adapters: Array<{
    adapter: string;
    status: "pending" | "running" | "succeeded" | "failed" | "skipped";
    attempts: number;
    processedItems: number;
    errorCode: string | null;
    errorMessage: string | null;
    updatedAt: string;
  }>;
  exportFilename: string | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
};

export type DataLifecycleOverview = {
  policy: RetentionPolicy;
  holds: LegalHold[];
  jobs: DataLifecycleJob[];
};

export type QuotaResource =
  | "concurrent_runs"
  | "concurrent_subagents"
  | "model_tokens"
  | "model_cost_micro_usd"
  | "mcp_requests"
  | "artifact_bytes"
  | "snapshot_bytes"
  | "active_previews"
  | "deployment_promotions";

export type StudioQuotaPolicy = {
  tenantId: string;
  policyId: string;
  revision: number;
  scope: {
    organizationId: string | null;
    teamId: string | null;
    userId: string | null;
    agentName: string | null;
    environment: string | null;
    apiKeyId: string | null;
  };
  limits: Partial<Record<QuotaResource, number>>;
  alertThresholds: Partial<Record<QuotaResource, number>>;
  updatedBy: string;
  updatedAt: string;
};

export type StudioQuotaUsage = {
  policies: StudioQuotaPolicy[];
  counters: Array<{
    tenantId: string;
    scopeKey: string;
    resource: QuotaResource;
    windowKey: string;
    reserved: number;
    committed: number;
    limit: number | null;
  }>;
  activeReservations: Array<{
    reservationId: string;
    resource: QuotaResource;
    amount: number;
    agentName: string | null;
    environment: string | null;
    subjectId: string;
    expiresAt: string;
  }>;
  unknownCostEntries: number;
  alerts: Array<{
    alertId: string;
    policyId: string;
    scopeKey: string;
    resource: QuotaResource;
    windowKey: string;
    thresholdPercent: number;
    usagePercent: number;
    used: number;
    limit: number;
    severity: "info" | "warning" | "critical";
  }>;
};

export type StudioDraftSummary = {
  parentDraftId?: string | null;
  draftId: string;
  agentId: string | null;
  spaceId: string | null;
  name: string;
  displayName: string;
  domain: string;
  version: string;
  template: StudioDraft["template"];
  goal?: string;
  primaryOutput?: string;
  primaryConstraint?: string | null;
  skillCount?: number;
  toolCount?: number;
  networkToolsEnabled?: boolean;
  revision: number;
  updatedAt: string;
  publishedVersion: string | null;
};

type StudioSpaceSummary = {
  space: { spaceId: string };
};

export type StudioSkillConversationMessage = {
  role: "user" | "assistant";
  content: string;
};

export type StudioSkillConversationReply = {
  status: "clarifying" | "ready";
  reply: string;
  skill: StudioSkill | null;
  followUpQuestions: string[];
};

export type StudioImportedSkill = {
  skill: StudioSkill;
  sourceContentHash: string;
  riskLevel: "low" | "review";
  findings: string[];
  warnings: string[];
};

export type StudioInstalledSkill = {
  draft: ApiAgentDraft;
  skillName: string;
  sourceContentHash: string;
  riskLevel: "low" | "review";
  findings: string[];
  warnings: string[];
  fileCount: number;
  binaryFileCount: number;
};

export type StudioPlatformSkillPackage = {
  packageId: string;
  revision: number;
  displayName: string;
  summary: string;
  tags: string[];
  compatibleRuntimes: StudioDraft["runtime"][];
  license: string;
  sourceUrl: string;
  sourceRevision: string;
  contentHash: string;
  riskLevel: "low" | "review";
  findings: string[];
  skill: StudioSkill;
  evaluationCases: Array<{
    id: string;
    tags: string[];
    prompt: string;
    expect: StudioEvalCase["expect"];
  }>;
};

export type StudioPlatformSkillCatalog = {
  revision: number;
  packages: StudioPlatformSkillPackage[];
};

type ApiEvalCase = {
  id: string;
  tags: string[];
  prompt: string;
  inputFiles: Array<{ path: string; mediaType: string }>;
  expect: StudioEvalCase["expect"];
};

type ApiDraftSpec = {
  name: string;
  version: string;
  displayName: string;
  description: string;
  domain: string;
  template: StudioDraft["template"];
  taskContract?: StudioDraft["taskContract"];
  runtime?: StudioDraft["runtime"];
  model: {
    routeId: string;
    model: string;
    reasoningEffort?: "minimal" | "low" | "medium" | "high" | "xhigh" | null;
    fallbackRouteId: string | null;
    fallbackModel: string | null;
    requiredCapabilities: string[];
  };
  systemPrompt: string;
  skills: Array<{
    name: string;
    description: string;
    instructions: string;
    files: Array<{
      path: string;
      content?: string | null;
      contentBase64?: string | null;
      retained?: boolean;
      sizeBytes?: number | null;
      contentSha256?: string | null;
      binary?: boolean;
    }>;
    fileCount?: number | null;
    filesTruncated?: boolean;
  }>;
  builtinTools: string[];
  pythonTools: StudioDraft["pythonTools"];
  mcpServers: string[];
  toolExposureMode: StudioDraft["toolExposureMode"];
  knowledgeReferences: string[];
  subagents: StudioDraft["subagents"];
  permissionPolicy: string;
  executionProfile: string;
  workspace: { restoreSession: boolean; archiveOnComplete: boolean };
  limits: {
    maxTurns: number | null;
    maxToolCalls: number | null;
    timeoutSeconds: number | null;
    maxBudgetUsd: number | null;
    maxModelTokens: number | null;
    maxSubagents: number;
    maxSubagentTasks: number;
    maxConcurrentSubagents: number;
    maxSubagentUsageUnits: number | null;
  };
  evaluationEnabled: boolean;
  evaluationCases: ApiEvalCase[];
};

export type ApiAgentDraft = {
  parentDraftId?: string | null;
  draftId: string;
  agentId: string | null;
  spaceId: string | null;
  tenantId: string;
  revision: number;
  spec: ApiDraftSpec;
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
  publishedVersion: string | null;
  publishedHash: string | null;
  publishedPackageHash: string | null;
};

export type StudioImportedAgentBundle = {
  draft: ApiAgentDraft;
  sourceContentHash: string;
  sourcePackageHash: string;
  lossless: boolean;
  roundTripVerified: boolean;
  warnings: string[];
};

export type ApiAgentVersion = {
  tenant_id: string;
  name: string;
  version: string;
  status: "published";
  manifest_hash: string;
  package_hash: string | null;
  created_at: string;
};

export type PersonalAgentVersion = {
  agent_id: string;
  name: string;
  version: string;
  display_name: string;
  manifest_hash: string;
  package_hash: string | null;
  created_at: string;
  current_version: string | null;
};

export type StudioKnowledgeAcl = {
  visibility: "tenant" | "restricted";
  userIds: string[];
  workloadIds: string[];
};

export type StudioKnowledgeSourceConfig =
  | {
    type: "file";
    documents: Array<{
      documentId: string;
      title: string;
      content: string;
      sourceUri: string | null;
    }>;
  }
  | {
    type: "web";
    url: string;
    title: string | null;
    maxBytes: number;
  };

export type StudioKnowledgeSource = {
  tenantId: string;
  reference: string;
  displayName: string;
  description: string;
  kind: "file" | "web";
  visibility: "tenant" | "restricted";
  revision: number;
  health: "pending" | "healthy" | "degraded" | "disabled";
  activeSnapshotId: string | null;
  lastSyncId: string | null;
  lastSyncAt: string | null;
  lastError: string | null;
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
};

export type KnowledgeBaseType = "rag" | "wiki" | "hybrid";
export type KnowledgeBaseEngine = "legacy" | "weknora";

export type StudioKnowledgeBase = {
  tenantId: string;
  reference: string;
  displayName: string;
  description: string;
  sourceReferences: string[];
  kbType: KnowledgeBaseType;
  engine: KnowledgeBaseEngine;
  engineRef: string;
  revision: number;
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
};

export type StudioKnowledgeDocumentStatus = {
  tenantId: string;
  sourceReference: string;
  documentId: string;
  title: string;
  parseStatus: string;
  summaryStatus: string;
  fileType: string;
  fileSize: number;
  enabled: boolean;
};

export type StudioKnowledgeWikiPage = {
  slug: string;
  title: string;
  pageType: string;
  content: string;
  summary: string;
  aliases: string[];
  categoryPath: string[];
  folderId: string;
};

export type StudioKnowledgeWikiGraph = {
  nodes: Array<{ slug: string; title: string; pageType: string; linkCount: number }>;
  links: Array<[string, string]>;
};

export type StudioKnowledgeWikiStats = {
  totalPages: number;
  pagesByType: Record<string, number>;
  totalLinks: number;
};

export type StudioKnowledgeDocumentChunk = {
  tenantId: string;
  sourceReference: string;
  documentId: string;
  chunkId: string;
  title: string;
  content: string;
  seq: number;
};

export type StudioKnowledgeSync = {
  tenantId: string;
  syncId: string;
  sourceReference: string;
  sourceRevision: number;
  status: "queued" | "running" | "succeeded" | "unchanged" | "failed";
  checkpointBefore: Record<string, string | number>;
  checkpointAfter: Record<string, string | number>;
  snapshotId: string | null;
  documentsSeen: number;
  chunksWritten: number;
  errorCode: string | null;
  errorMessage: string | null;
  createdBy: string;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
};

export type StudioKnowledgeHit = {
  content: string;
  score: number;
  trust: "sensitive" | "untrusted";
  citation: {
    knowledgeBaseReference: string;
    sourceReference: string;
    sourceDisplayName: string;
    snapshotId: string;
    documentId: string;
    chunkId: string;
    title: string;
    uri: string;
  };
  matchedTerms: string[];
};

export type StudioConnectionScope = "personal" | "team" | "workload";

export type StudioCredentialConnection = {
  tenantId: string;
  connectionId: string;
  displayName: string;
  resourceKind: "model" | "mcp";
  resourceReference: string;
  scope: StudioConnectionScope;
  principalId: string;
  secretReference: string;
  requiredKeys: string[];
  status: "active" | "revoked";
  revision: number;
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
  revokedAt: string | null;
};

export type StudioContextTrust = "safe" | "sensitive" | "untrusted";

export type StudioCallPolicyRule = {
  name: string;
  decision: "allow" | "ask" | "deny";
  tenantId?: string | null;
  agentName?: string | null;
  tool?: string | null;
  pathGlob?: string | null;
  commandContains?: string | null;
  sandboxIsolation?: "workspace" | "container" | null;
  contextTrust?: StudioContextTrust | null;
  priority: number;
};

export type StudioResultPolicyRule = {
  name: string;
  trust: StudioContextTrust;
  tool: string;
  agentName?: string | null;
  priority: number;
};

export type StudioGovernedPolicy = {
  tenantId: string;
  policyId: string;
  displayName: string;
  description: string;
  callRules: StudioCallPolicyRule[];
  resultRules: StudioResultPolicyRule[];
  revision: number;
  publishedRevision: number | null;
  publishedHash: string | null;
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
};

export type StudioPolicyScenario = {
  scenarioId: string;
  agentName: string;
  toolName: string;
  arguments: Record<string, unknown>;
  sandboxIsolation: "workspace" | "container";
  contextTrust: StudioContextTrust;
};

export type StudioPolicySimulation = {
  scenarioId: string;
  call: {
    decision: "allow" | "ask" | "deny";
    rule_name: string;
    reason: string;
  };
  result: {
    trust: StudioContextTrust;
    rule_name: string;
    reason: string;
  };
};

export type StudioPolicyImpact = {
  policyId: string;
  draftRevision: number;
  publishedRevision: number | null;
  scenarioCount: number;
  changedCount: number;
  items: Array<{
    scenarioId: string;
    before: StudioPolicySimulation;
    after: StudioPolicySimulation;
    changed: boolean;
  }>;
};

export type StudioPolicyPublication = {
  tenantId: string;
  policyId: string;
  revision: number;
  contentHash: string;
  displayName: string;
  description: string;
  callRules: StudioCallPolicyRule[];
  resultRules: StudioResultPolicyRule[];
  publishedBy: string;
  publishedAt: string;
};

export type StudioAgentBuilderPatch = {
  baseRevision: number;
  taskContract: NonNullable<StudioDraft["taskContract"]>;
  systemPrompt: string;
  evaluationCases: StudioEvalCase[];
  explanation: string[];
  validation: StudioValidation;
};

export type StudioBuilderChanges = {
  displayName?: string;
  description?: string;
  systemPrompt?: string;
  taskContract?: NonNullable<StudioDraft["taskContract"]>;
  builtinTools?: string[];
  mcpServers?: string[];
  knowledgeReferences?: string[];
  skillInstructions?: { name: string; instructions: string }[];
  removeSkills?: string[];
  roleResponsibilities?: { alias: string; responsibility: string }[];
};
export type StudioBuilderReply = {
  baseRevision: number;
  reply: string;
  changedFields: string[];
  changes: StudioBuilderChanges;
  action?: "edit" | "run" | "rerun" | "ask" | "reply";
  task?: string;
};

export type StudioValidation = {
  ready: boolean;
  productionEligible: boolean;
  issues: Array<{
    code: string;
    message: string;
    severity: "error" | "warning";
    path: string | null;
    stage: "publish" | "production";
    relatedReferences: string[];
    suggestedProfileIds: string[];
  }>;
  contentHash: string | null;
  packageHash: string | null;
  runtimeCompatibility: {
    runtime: StudioDraft["runtime"];
    label: string;
    stability: "stable" | "preview" | "experimental";
    compatible: boolean;
    capabilities: string[];
    limitations: string[];
  };
};

export type StudioPreflightCheck = {
  stage: "bundle" | "sandbox_provision" | "sandbox_prepare" | "model" | "mcp" | "approval" | "workspace_artifact" | "cleanup";
  status: "passed" | "failed" | "skipped" | "cancelled" | "timed_out";
  startedAt: string;
  completedAt: string;
  durationMs: number;
  summary: string;
  errorCode: string | null;
  details: Record<string, string | number | boolean>;
};

export type StudioPreflightResult = {
  schemaVersion: "harness.preflight/v1";
  previewId: string;
  status: "passed" | "failed" | "cancelled" | "timed_out";
  startedAt: string;
  completedAt: string;
  checks: StudioPreflightCheck[];
  events: Array<{
    sequence: number;
    eventType: "check.started" | "check.completed";
    stage: StudioPreflightCheck["stage"];
    occurredAt: string;
    status: StudioPreflightCheck["status"] | null;
    errorCode: string | null;
  }>;
  errorCode: string | null;
  artifact: {
    name: string;
    mediaType: string;
    sha256: string;
    sizeBytes: number;
  } | null;
};

export type StudioPreview = {
  previewId: string;
  tenantId: string;
  draftId: string;
  draftRevision: number;
  contentHash: string;
  packageHash: string;
  requestedBy: string;
  idempotencyKey: string;
  status: "queued" | "provisioning" | "ready" | "cancelling" | "cancelled" | "failed" | "expired";
  identityKind: "test";
  environment: "preview";
  executionProfile: string;
  executionProfileVersion: number;
  fencingToken: number;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
  errorCode: string | null;
  preflightResult: StudioPreflightResult | null;
  stale: boolean;
  staleReason: string | null;
};

export type StudioTaskDrivenRecommendation = {
  runtime: StudioDraft["runtime"];
  modelRouteId: string;
  model: string;
  template: StudioDraft["template"];
  builtinTools: string[];
  mcpServers: string[];
  permissionPolicy: string;
  executionProfile: string;
  reasons: string[];
  validation: StudioValidation;
};

export type StudioTaskDrivenDraftResult = {
  draft: ApiAgentDraft;
  recommendation: StudioTaskDrivenRecommendation | null;
};

export type CodexLoopStage = {
  id: "plan" | "tools" | "correction" | "verification" | "result";
  label: string;
  status: "pending" | "active" | "completed" | "skipped" | "failed";
  summary: string;
  evidence: Array<{
    eventType: string;
    sequence: number;
    summary: string;
  }>;
};

export type StudioTryRun = {
  activity?: RunActivity | null;
  draftId: string;
  draftRevision: number;
  run: {
    run_id: string;
    session_id: string;
    status: "queued" | "provisioning" | "running" | "waiting_approval" | "cancelling" | "cancelled" | "succeeded" | "failed" | "timed_out" | "rejected";
    error_code: string | null;
  };
  events: Array<{
    event_id: string;
    sequence: number;
    type: string;
    timestamp: string;
    payload: Record<string, unknown>;
  }>;
  approvals: Array<{
    approval_id: string;
    status: "pending" | "approved" | "rejected" | "expired" | "cancelled";
    tool_name: string | null;
    reason: string;
    argument_summary: Record<string, unknown>;
    risk: string | null;
  }>;
  artifacts: Array<{
    artifact_id: string;
    name: string;
    media_type: string;
    status: "pending" | "ready" | "failed";
    size_bytes: number | null;
  }>;
  finalText: string;
  loop: CodexLoopStage[];
};

export type StudioEvalDataset = {
  tenantId: string;
  datasetId: string;
  version: number;
  name: string;
  agentName: string;
  required: boolean;
  sourceDraftId: string;
  sourceDraftRevision: number;
  sourceContentHash: string;
  sourcePackageHash: string;
  cases: ApiEvalCase[];
  fixtures: Array<{
    path: string;
    mediaType: string;
    objectId: string;
    sha256: string;
    sizeBytes: number;
  }>;
  createdBy: string;
  createdAt: string;
};

export type StudioSolidifiedAgentResult = {
  draft: ApiAgentDraft;
  version: ApiAgentVersion;
  dataset: StudioEvalDataset;
  loop: CodexLoopStage[];
};

export type StudioEvalCaseResult = {
  tenantId: string;
  evalRunId: string;
  caseId: string;
  sessionId: string;
  runId: string;
  status: "passed" | "failed" | "error" | "timed_out" | "cancelled";
  passed: boolean;
  durationSeconds: number;
  failures: string[];
  tools: string[];
  approvalRequested: boolean;
  completedAt: string;
};

export type StudioEvalRun = {
  run: {
    tenantId: string;
    evalRunId: string;
    datasetId: string;
    datasetVersion: number;
    agentName: string;
    agentVersion: string;
    previewId: string | null;
    environment: string | null;
    requestedBy: string;
    idempotencyKey: string;
    status: "queued" | "running" | "cancelling" | "cancelled" | "passed" | "failed";
    fencingToken: number;
    nextCaseIndex: number;
    activeCaseId: string | null;
    activeSessionId: string | null;
    activeInputArtifactIds: string[];
    activeRunId: string | null;
    activeStartedAt: string | null;
    createdAt: string;
    updatedAt: string;
    completedAt: string | null;
    errorCode: string | null;
    artifacts: Array<{
      artifactId: string;
      name: string;
      mediaType: string;
      sha256: string;
      sizeBytes: number;
    }>;
  };
  cases: StudioEvalCaseResult[];
  passedCases: number;
  totalCases: number;
};

export type StudioEvalGate = {
  agentName: string;
  agentVersion: string;
  passed: boolean;
  requiredDatasets: number;
  passedDatasets: number;
  missingDatasetIds: string[];
};

export type StudioEnvironmentName = "test" | "canary" | "production";

export type StudioCredentialScope = "user" | "team" | "workload";

export type StudioEnvironmentResourcePolicy = {
  executionProfileId: string;
  executionProfileVersion: number;
  networkProfileId: string;
  networkProfileVersion: number;
  networkAccess: Array<"none" | "internal" | "external">;
  allowedModelRoutes: string[];
  capabilityCatalogRevision: number;
  allowedMcpReferences: string[];
  allowedKnowledgeReferences: string[];
  credentialScopes: StudioCredentialScope[];
  quota: {
    maxRunBudgetUsd: number | null;
    maxModelTokens: number | null;
    maxArtifactBytes: number | null;
  };
};

export type StudioEnvironmentPolicySnapshot = {
  environment: StudioEnvironmentName;
  environmentRevision: number;
  policyRevision: number;
  policyHash: string;
  resourcePolicy: StudioEnvironmentResourcePolicy;
  capturedAt: string;
};

export type StudioEnvironment = {
  tenantId: string;
  agentName: string;
  name: StudioEnvironmentName;
  revision: number;
  policyRevision: number;
  policyHash: string;
  resourcePolicy: StudioEnvironmentResourcePolicy;
  routes: Array<{ snapshotId: string; weight: number }>;
  healthySnapshotId: string | null;
  updatedAt: string;
};

export type StudioAgentTrigger = {
  tenantId: string;
  triggerId: string;
  kind: "webhook" | "a2a" | "schedule" | "chatops";
  name: string;
  agentName: string;
  environment: StudioEnvironmentName;
  enabled: boolean;
  revision: number;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  lastInvokedAt: string | null;
  nextFireAt: string | null;
  schedule: {
    intervalSeconds: number;
    timezone: string;
    prompt: string;
  } | null;
  chatops: {
    provider: "slack" | "teams" | "email" | "generic";
    allowedChannelIds: string[];
  } | null;
};

export type StudioCreatedAgentTrigger = {
  trigger: StudioAgentTrigger;
  secret: string;
};

export type StudioDeploymentSnapshot = {
  tenantId: string;
  snapshotId: string;
  agentName: string;
  agentVersion: string;
  environment: StudioEnvironmentName;
  manifestHash: string;
  packageHash: string;
  imageDigest: string;
  executionProfile: string;
  executionProfileVersion: number;
  executionProfileHash: string;
  environmentPolicySnapshot: StudioEnvironmentPolicySnapshot | null;
  config: Record<string, string | number | boolean>;
  evalGatePassed: boolean;
  evalRequiredDatasets: number;
  previewId: string | null;
  createdBy: string;
  createdAt: string;
};

export type StudioDeployment = {
  deployment: {
    tenantId: string;
    deploymentId: string;
    agentName: string;
    environment: StudioEnvironmentName;
    action: "promote" | "rollback";
    targetSnapshotId: string;
    previousSnapshotId: string | null;
    canaryPercent: number;
    expectedEnvironmentRevision: number;
    idempotencyKey: string;
    requestedBy: string;
    status: "queued" | "reconciling" | "succeeded" | "failed";
    fencingToken: number;
    createdAt: string;
    updatedAt: string;
    completedAt: string | null;
    errorCode: string | null;
  };
  target: StudioDeploymentSnapshot;
  environment: StudioEnvironment;
};

export type StudioQualityScore = {
  tenantId: string;
  scoreId: string;
  runId: string;
  traceId: string;
  sessionId: string;
  agentName: string;
  agentVersion: string;
  deploymentSnapshotId: string | null;
  evalRunId: string | null;
  name: string;
  value: number;
  source: "rule" | "human" | "llm_judge";
  createdBy: string;
  createdAt: string;
};

export type StudioQualityIncident = {
  tenantId: string;
  incidentId: string;
  ruleId: string;
  agentName: string;
  agentVersion: string;
  state: "open" | "resolved";
  observedValue: number;
  sampleCount: number;
  openedAt: string;
  resolvedAt: string | null;
};

export type StudioQualityRule = {
  tenantId: string;
  ruleId: string;
  agentName: string;
  scoreName: string;
  minimumValue: number;
  minimumSamples: number;
  blocksPromotion: boolean;
  enabled: boolean;
  dashboardUrl: string | null;
  createdAt: string;
};

export type StudioQualityGate = {
  agentName: string;
  agentVersion: string;
  passed: boolean;
  blockingIncidentIds: string[];
};

export type StudioCapabilities = {
  modelRoutes: Array<{
    routeId: string;
    label: string;
    provider: string;
    models: string[];
    capabilities: string[];
    modelType?: "chat" | "vision" | "image_generation" | "video_generation";
    apiFormat: "anthropic_compatible" | "openai_compatible" | "openai_images" | "openai_videos";
    enabled: boolean;
  }>;
  builtinTools: Array<{
    name: string;
    label: string;
    description: string;
    risk: StudioRisk;
    approvalBehavior: string;
  }>;
  mcpServers: Array<{
    reference: string;
    ownerUserId: string | null;
    allowedExecutionProfileIds: string[];
    category: "tool" | "knowledge";
    serverName: string | null;
    label: string;
    description: string;
    endpointUrl: string | null;
    transport: "http" | "sse";
    customHeaders: Record<string, string>;
    tools: string[];
    risk: StudioRisk;
    networkAccess: McpOption["network"];
    sendsUserData: boolean;
    readOnly: boolean;
    credentialManaged: boolean;
    executionLocation: string;
    preflightRequired: boolean;
    credentialReference: string | null;
    authMode: "none" | "bearer" | "header" | "query";
    authName: string | null;
    authKey: string;
    version: number;
    enabled: boolean;
  }>;
  policies: Array<{ policyId: string; label: string; description: string; enabled: boolean }>;
  executionProfiles: Array<{
    profileId: string;
    label: string;
    description: string;
    sandboxProvider: "local" | "daytona" | "e2b" | "gvisor";
    networkAccess: Array<"none" | "internal" | "external">;
    cpuMillis: number;
    memoryMiB: number;
    diskMiB: number;
    ttlSeconds: number;
    networkPolicyId: string;
    allowedMcpReferences: string[];
    providerConfigReference: string;
    productionAllowed: boolean;
    version: number;
    enabled: boolean;
  }>;
  templates: Array<{
    template: StudioDraft["template"];
    label: string;
    description: string;
  }>;
  runtimeCapabilities: Array<{
    runtime: StudioDraft["runtime"];
    label: string;
    stability: "stable" | "preview" | "experimental";
    capabilities: string[];
    modelApiFormats: Array<"anthropic_compatible" | "openai_compatible" | "openai_images">;
    limitations: string[];
  }>;
};

export type StudioCapabilityCatalogRecord = {
  tenantId: string;
  revision: number;
  catalog: StudioCapabilities;
  updatedBy: string;
  updatedAt: string;
};

export type StudioCatalogImpact = {
  resourceType: "modelRoute" | "mcp" | "policy" | "executionProfile";
  resourceId: string;
  draftIds: string[];
};

export type StudioCatalogMutationResult = {
  record: StudioCapabilityCatalogRecord;
  impact: StudioCatalogImpact;
};

export type StudioMcpDiscoveryResult = {
  endpointUrl: string;
  transport: "http" | "sse";
  serverName: string;
  serverTitle: string | null;
  serverVersion: string | null;
  latencyMs: number;
  tools: Array<{
    name: string;
    canonicalName: string;
    title: string | null;
    description: string;
  }>;
};

export type StudioMcpCredentialStatus = {
  reference: string;
  configured: boolean;
  keyNames: string[];
  revision: number | null;
  updatedBy: string | null;
  updatedAt: string | null;
};

export class StudioApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "StudioApiError";
  }
}

async function errorFrom(response: Response): Promise<StudioApiError> {
  let code = `http_${response.status}`;
  let message = `请求失败（${response.status}）`;
  try {
    const payload = (await response.json()) as {
      error?: { code?: string; message?: string };
      detail?: { code?: string; message?: string } | string;
    };
    const detail = typeof payload.detail === "object" ? payload.detail : undefined;
    code = payload.error?.code ?? detail?.code ?? code;
    message =
      payload.error?.message
      ?? detail?.message
      ?? (typeof payload.detail === "string" ? payload.detail : message);
  } catch {}
  return new StudioApiError(response.status, code, message);
}

async function requestForm<T>(path: string, form: FormData): Promise<T> {
  const response = requireAuthenticatedResponse(
    await fetch(`/api/studio/${path.replace(/^\//, "")}`, {
      method: "POST",
      cache: "no-store",
      body: form,
    }),
  );
  if (!response.ok) throw await errorFrom(response);
  return response.json() as Promise<T>;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = requireAuthenticatedResponse(
    await fetch(`/api/studio/${path.replace(/^\//, "")}`, {
      ...init,
      cache: "no-store",
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    }),
  );
  if (!response.ok) throw await errorFrom(response);
  return response.json() as Promise<T>;
}

async function listAccessibleDrafts(): Promise<StudioDraftSummary[]> {
  if (!TEAM_COLLABORATION_ENABLED) {
    const personal = await request<StudioDraftSummary[]>("drafts");
    return personal.filter((draft) => !draft.spaceId);
  }
  const personalRequest = request<StudioDraftSummary[]>("drafts");
  try {
    const [personal, response] = await Promise.all([
      personalRequest,
      fetch("/api/spaces", { cache: "no-store" }).then(requireAuthenticatedResponse),
    ]);
    if (!response.ok) return personal;
    const spaces = await response.json() as StudioSpaceSummary[];
    const workspace = await Promise.all(
      spaces.map((item) => request<StudioDraftSummary[]>(
        `drafts?spaceId=${encodeURIComponent(item.space.spaceId)}`,
      )),
    );
    const unique = new Map(
      [...personal, ...workspace.flat()].map((draft) => [draft.draftId, draft]),
    );
    return [...unique.values()].sort((left, right) =>
      right.updatedAt.localeCompare(left.updatedAt)
    );
  } catch {
    // Personal Studio remains usable if collaboration is temporarily degraded.
    return personalRequest;
  }
}

const studioDraftRequests = new Map<string, Promise<ApiAgentDraft>>();
const studioDraftSnapshots = new Map<
  string,
  { receivedAt: number; draft: ApiAgentDraft }
>();
const STUDIO_DRAFT_CACHE_MS = 10_000;

function rememberStudioDraft(draft: ApiAgentDraft): ApiAgentDraft {
  studioDraftSnapshots.set(draft.draftId, {
    receivedAt: Date.now(),
    draft,
  });
  return draft;
}

function forgetStudioDraft(draftId: string) {
  studioDraftSnapshots.delete(draftId);
}

function getStudioDraft(
  draftId: string,
  options: { expectedRevision?: number; maxAgeMs?: number } = {},
): Promise<ApiAgentDraft> {
  const cached = studioDraftSnapshots.get(draftId);
  const maxAgeMs = options.maxAgeMs ?? STUDIO_DRAFT_CACHE_MS;
  if (
    cached
    && Date.now() - cached.receivedAt < maxAgeMs
    && (
      options.expectedRevision === undefined
      || cached.draft.revision === options.expectedRevision
    )
  ) {
    return Promise.resolve(cached.draft);
  }
  const inFlight = studioDraftRequests.get(draftId);
  if (inFlight) return inFlight;
  const requestDraft = request<ApiAgentDraft>(
    `drafts/${encodeURIComponent(draftId)}`,
  ).then(rememberStudioDraft).finally(() => {
    if (studioDraftRequests.get(draftId) === requestDraft) {
      studioDraftRequests.delete(draftId);
    }
  });
  studioDraftRequests.set(draftId, requestDraft);
  return requestDraft;
}

async function agentRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = requireAuthenticatedResponse(
    await fetch(`/api/harness/agents/${path.replace(/^\//, "")}`, {
      ...init,
      cache: "no-store",
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    }),
  );
  if (!response.ok) throw await errorFrom(response);
  return response.json() as Promise<T>;
}

async function harnessRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = requireAuthenticatedResponse(
    await fetch(`/api/harness/${path.replace(/^\//, "")}`, {
      ...init,
      cache: "no-store",
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    }),
  );
  if (!response.ok) throw await errorFrom(response);
  return response.json() as Promise<T>;
}

async function lifecycleRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = requireAuthenticatedResponse(
    await fetch(`/api/data-lifecycle/${path.replace(/^\//, "")}`, {
      ...init,
      cache: "no-store",
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    }),
  );
  if (!response.ok) throw await errorFrom(response);
  return response.json() as Promise<T>;
}

export const lifecycleClient = {
  overview: () => lifecycleRequest<DataLifecycleOverview>("overview"),
  selfJobs: () => lifecycleRequest<DataLifecycleJob[]>("self/jobs"),
  replacePolicy: (
    policy: RetentionPolicy,
    values: Pick<RetentionPolicy, "sessionDays" | "artifactDays" | "traceDays" | "evalDays">,
  ) => lifecycleRequest<RetentionPolicy>("retention-policy", {
    method: "PUT",
    body: JSON.stringify({
      expectedRevision: policy.revision,
      sessionDays: values.sessionDays,
      artifactDays: values.artifactDays,
      traceDays: values.traceDays,
      evalDays: values.evalDays,
    }),
  }),
  createHold: (scope: LifecycleScope, reason: string) =>
    lifecycleRequest<LegalHold>("legal-holds", {
      method: "POST",
      body: JSON.stringify({ scope, reason }),
    }),
  releaseHold: (holdId: string) =>
    lifecycleRequest<LegalHold>(`legal-holds/${encodeURIComponent(holdId)}/release`, {
      method: "POST",
    }),
  createJob: (
    kind: DataLifecycleJob["kind"],
    scope: LifecycleScope,
    idempotencyKey: string,
  ) => lifecycleRequest<DataLifecycleJob>("jobs", {
    method: "POST",
    body: JSON.stringify({ kind, scope, idempotencyKey }),
  }),
  getJob: (jobId: string) =>
    lifecycleRequest<DataLifecycleJob>(`jobs/${encodeURIComponent(jobId)}`),
  retryJob: (jobId: string) =>
    lifecycleRequest<DataLifecycleJob>(`jobs/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
    }),
};

export function apiDraftToStudioDraft(source: ApiAgentDraft): StudioDraft {
  const spec = source.spec;
  return {
    parentDraftId: source.parentDraftId ?? null,
    id: source.draftId,
    agentId: source.agentId ?? null,
    spaceId: source.spaceId ?? null,
    revision: source.revision,
    publishedVersion: source.publishedVersion,
    publishedHash: source.publishedHash,
    publishedPackageHash: source.publishedPackageHash,
    displayName: spec.displayName,
    name: spec.name,
    description: spec.description,
    domain: spec.domain,
    version: spec.version,
    template: spec.template,
    taskContract: spec.taskContract ?? null,
    runtime: spec.runtime ?? "claude-agent-sdk",
    modelRoute: spec.model.routeId,
    model: spec.model.model,
    reasoningEffort: spec.model.reasoningEffort ?? null,
    requiredCapabilities: spec.model.requiredCapabilities,
    systemPrompt: spec.systemPrompt,
    skills: spec.skills,
    builtinTools: spec.builtinTools,
    pythonTools: spec.pythonTools ?? [],
    mcpServers: spec.mcpServers,
    toolExposureMode: spec.toolExposureMode,
    knowledgeReferences: spec.knowledgeReferences ?? [],
    subagents: spec.subagents,
    policy: spec.permissionPolicy,
    executionProfile: spec.executionProfile,
    restoreSession: spec.workspace.restoreSession,
    archiveOnComplete: spec.workspace.archiveOnComplete,
    maxTurns: spec.limits.maxTurns,
    maxToolCalls: spec.limits.maxToolCalls ?? 256,
    timeoutSeconds: spec.limits.timeoutSeconds,
    maxBudgetUsd: spec.limits.maxBudgetUsd,
    maxModelTokens: spec.limits.maxModelTokens,
    maxSubagents: spec.limits.maxSubagents,
    maxSubagentTasks: spec.limits.maxSubagentTasks,
    maxConcurrentSubagents: spec.limits.maxConcurrentSubagents,
    maxSubagentUsageUnits: spec.limits.maxSubagentUsageUnits,
    evaluationEnabled: spec.evaluationEnabled ?? true,
    evalCases: spec.evaluationCases.map((item) => ({
      id: item.id,
      label: item.id,
      tag: (item.tags.find((tag) => ["happy", "ambiguous", "safety"].includes(tag)) ?? "happy") as StudioEvalCase["tag"],
      prompt: item.prompt,
      expect: item.expect,
    })),
  };
}

export function studioDraftToSpec(draft: StudioDraft): ApiDraftSpec {
  return {
    name: draft.name,
    version: draft.version,
    displayName: draft.displayName,
    description: draft.description,
    domain: draft.domain,
    template: draft.template,
    taskContract: draft.taskContract,
    runtime: draft.runtime,
    model: {
      routeId: draft.modelRoute,
      model: draft.model,
      reasoningEffort: draft.reasoningEffort,
      fallbackRouteId: null,
      fallbackModel: null,
      requiredCapabilities: draft.requiredCapabilities,
    },
    systemPrompt: draft.systemPrompt,
    skills: draft.skills.map((skill) => ({ ...skill, files: skill.files ?? [] })),
    builtinTools: draft.builtinTools,
    pythonTools: draft.pythonTools,
    mcpServers: draft.mcpServers,
    toolExposureMode: draft.toolExposureMode,
    knowledgeReferences: draft.knowledgeReferences,
    subagents: draft.subagents,
    permissionPolicy: draft.policy,
    executionProfile: draft.executionProfile,
    workspace: {
      restoreSession: draft.restoreSession,
      archiveOnComplete: draft.archiveOnComplete,
    },
    limits: {
      maxTurns: draft.maxTurns,
      maxToolCalls: draft.maxToolCalls,
      timeoutSeconds: draft.timeoutSeconds,
      maxBudgetUsd: draft.maxBudgetUsd,
      maxModelTokens: draft.maxModelTokens,
      maxSubagents: draft.maxSubagents,
      maxSubagentTasks: draft.maxSubagentTasks,
      maxConcurrentSubagents: draft.maxConcurrentSubagents,
      maxSubagentUsageUnits: draft.maxSubagentUsageUnits,
    },
    evaluationEnabled: draft.evaluationEnabled,
    evaluationCases: draft.evalCases.map((item) => ({
      id: item.id,
      tags: [item.tag, draft.domain],
      prompt: item.prompt,
      inputFiles: [],
      expect: item.expect,
    })),
  };
}

export const studioClient = {
  listConnections: () =>
    request<StudioCredentialConnection[]>("governance/connections"),
  createConnection: (values: {
    connectionId: string;
    displayName: string;
    resourceKind: "model" | "mcp";
    resourceReference: string;
    scope: StudioConnectionScope;
    principalId: string;
    secretReference: string;
    requiredKeys: string[];
  }) => request<StudioCredentialConnection>("governance/connections", {
    method: "POST",
    body: JSON.stringify(values),
  }),
  replaceConnection: (
    connection: StudioCredentialConnection,
    values: Pick<
      StudioCredentialConnection,
      "displayName" | "secretReference" | "requiredKeys"
    >,
  ) => request<StudioCredentialConnection>(
    `governance/connections/${encodeURIComponent(connection.connectionId)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        expectedRevision: connection.revision,
        ...values,
      }),
    },
  ),
  revokeConnection: (connection: StudioCredentialConnection) =>
    request<StudioCredentialConnection>(
      `governance/connections/${encodeURIComponent(connection.connectionId)}/revoke`,
      {
        method: "POST",
        body: JSON.stringify({ expectedRevision: connection.revision }),
      },
    ),
  listGovernedPolicies: () =>
    request<StudioGovernedPolicy[]>("governance/policies"),
  createGovernedPolicy: (values: {
    policyId: string;
    displayName: string;
    description: string;
    callRules: StudioCallPolicyRule[];
    resultRules: StudioResultPolicyRule[];
  }) => request<StudioGovernedPolicy>("governance/policies", {
    method: "POST",
    body: JSON.stringify(values),
  }),
  replaceGovernedPolicy: (
    policy: StudioGovernedPolicy,
    values: Pick<
      StudioGovernedPolicy,
      "displayName" | "description" | "callRules" | "resultRules"
    >,
  ) => request<StudioGovernedPolicy>(
    `governance/policies/${encodeURIComponent(policy.policyId)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        expectedRevision: policy.revision,
        ...values,
      }),
    },
  ),
  simulateGovernedPolicy: (
    policyId: string,
    scenario: StudioPolicyScenario,
  ) => request<StudioPolicySimulation>(
    `governance/policies/${encodeURIComponent(policyId)}/simulate`,
    {
      method: "POST",
      body: JSON.stringify({ scenario }),
    },
  ),
  previewGovernedPolicyImpact: (
    policyId: string,
    scenarios: StudioPolicyScenario[],
  ) => request<StudioPolicyImpact>(
    `governance/policies/${encodeURIComponent(policyId)}/impact`,
    {
      method: "POST",
      body: JSON.stringify({ scenarios }),
    },
  ),
  publishGovernedPolicy: (policy: StudioGovernedPolicy) =>
    request<StudioPolicyPublication>(
      `governance/policies/${encodeURIComponent(policy.policyId)}/publish`,
      {
        method: "POST",
        body: JSON.stringify({ expectedRevision: policy.revision }),
      },
    ),
  listPolicyPublications: (policyId: string) =>
    request<StudioPolicyPublication[]>(
      `governance/policies/${encodeURIComponent(policyId)}/publications`,
    ),
  listKnowledgeBases: () =>
    request<StudioKnowledgeBase[]>("knowledge/bases"),
  getKnowledgeBase: (reference: string) =>
    request<StudioKnowledgeBase>(
      `knowledge/bases/${encodeURIComponent(reference)}`,
    ),
  createKnowledgeBase: (
    values: Pick<
      StudioKnowledgeBase,
      "reference" | "displayName" | "description" | "sourceReferences"
    > & {
      kbType?: KnowledgeBaseType;
      engine?: KnowledgeBaseEngine;
    },
  ) => request<StudioKnowledgeBase>("knowledge/bases", {
    method: "POST",
    body: JSON.stringify(values),
  }),
  listKnowledgeDocuments: (baseReference: string) =>
    request<StudioKnowledgeDocumentStatus[]>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/documents`,
    ),
  createKnowledgeDocument: (
    baseReference: string,
    body: { title: string; content: string },
  ) =>
    request<StudioKnowledgeDocumentStatus>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/documents`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  uploadKnowledgeDocument: (baseReference: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<StudioKnowledgeDocumentStatus>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/documents/upload`,
      form,
    );
  },
  deleteKnowledgeDocument: (baseReference: string, documentId: string) =>
    request<void>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/documents/${encodeURIComponent(documentId)}`,
      { method: "DELETE" },
    ),
  reparseKnowledgeDocument: (baseReference: string, documentId: string) =>
    request<StudioKnowledgeDocumentStatus>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/documents/${encodeURIComponent(documentId)}/reparse`,
      { method: "POST" },
    ),
  listKnowledgeDocumentChunks: (baseReference: string, documentId: string) =>
    request<StudioKnowledgeDocumentChunk[]>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/documents/${encodeURIComponent(documentId)}/chunks`,
    ),
  getKnowledgeDocumentChunk: (baseReference: string, chunkId: string) =>
    request<StudioKnowledgeDocumentChunk>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/chunks/${encodeURIComponent(chunkId)}`,
    ),
  listWikiPages: (baseReference: string) =>
    request<StudioKnowledgeWikiPage[]>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/wiki/pages`,
    ),
  getWikiPage: (baseReference: string, slug: string) =>
    request<StudioKnowledgeWikiPage>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/wiki/pages/${slug
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
    ),
  searchWikiPages: (baseReference: string, query: string) =>
    request<StudioKnowledgeWikiPage[]>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/wiki/search?q=${encodeURIComponent(query)}`,
    ),
  getWikiGraph: (baseReference: string) =>
    request<StudioKnowledgeWikiGraph>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/wiki/graph`,
    ),
  getWikiStats: (baseReference: string) =>
    request<StudioKnowledgeWikiStats>(
      `knowledge/sources/${encodeURIComponent(baseReference)}/wiki/stats`,
    ),
  replaceKnowledgeBase: (
    value: StudioKnowledgeBase,
    update: Pick<
      StudioKnowledgeBase,
      "displayName" | "description" | "sourceReferences"
    >,
  ) => request<StudioKnowledgeBase>(
    `knowledge/bases/${encodeURIComponent(value.reference)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        expectedRevision: value.revision,
        ...update,
      }),
    },
  ),
  listKnowledgeSources: () =>
    request<StudioKnowledgeSource[]>("knowledge/sources"),
  createKnowledgeSource: (body: {
    reference: string;
    displayName: string;
    description?: string;
    kind: "file" | "web";
    config: StudioKnowledgeSourceConfig;
    acl?: StudioKnowledgeAcl;
    syncNow?: boolean;
  }) => request<{
    source: StudioKnowledgeSource;
    sync: StudioKnowledgeSync | null;
  }>("knowledge/sources", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  syncKnowledgeSource: (reference: string) =>
    request<StudioKnowledgeSync>(
      `knowledge/sources/${encodeURIComponent(reference)}/sync`,
      { method: "POST" },
    ),
  searchKnowledge: (
    query: string,
    knowledgeBaseReferences: string[],
    limit = 8,
  ) => request<{ hits: StudioKnowledgeHit[]; searchedSnapshotIds: string[] }>(
    "knowledge/search",
    {
      method: "POST",
      body: JSON.stringify({ query, knowledgeBaseReferences, limit }),
    },
  ),
  quotaUsage: () => request<StudioQuotaUsage>("quotas"),
  replaceQuotaPolicy: (
    policyId: string,
    expectedRevision: number,
    limits: Partial<Record<QuotaResource, number>>,
    scope: { agentName: string | null; environment: string | null } = {
      agentName: null,
      environment: null,
    },
  ) => request<StudioQuotaPolicy>(`quotas/${encodeURIComponent(policyId)}`, {
    method: "PUT",
    body: JSON.stringify({ expectedRevision, scope, limits }),
  }),
  createInternalSubagent: (parentId: string, expectedRevision: number, displayName: string, responsibility: string) =>
    request<{ parent: ApiAgentDraft; child: ApiAgentDraft }>(`drafts/${encodeURIComponent(parentId)}/subagents`, {
      method: "POST", body: JSON.stringify({ expectedRevision, displayName, responsibility }),
    }).then((result) => {
      rememberStudioDraft(result.parent);
      rememberStudioDraft(result.child);
      return result;
    }),
  setDraftPlacement: (draftId: string, expectedRevision: number, parentDraftId: string | null) =>
    request<ApiAgentDraft>(`drafts/${encodeURIComponent(draftId)}/placement`, {
      method: "PUT", body: JSON.stringify({ expectedRevision, parentDraftId }),
    }).then(rememberStudioDraft),
  listDrafts: () => request<StudioDraftSummary[]>("drafts"),
  listAccessibleDrafts,
  getDraft: (
    draftId: string,
    options?: { expectedRevision?: number; maxAgeMs?: number },
  ) => getStudioDraft(draftId, options),
  prefetchDraft: (draftId: string, expectedRevision?: number) =>
    getStudioDraft(draftId, { expectedRevision }).then(() => undefined),
  capabilities: () => request<StudioCapabilities>("capabilities"),
  continueSkillConversation: (body: {
    modelRoute: string;
    context: {
      agentName: string;
      displayName: string;
      domain: string;
      description: string;
      currentSkill: StudioSkill;
    };
    messages: StudioSkillConversationMessage[];
  }) => request<StudioSkillConversationReply>("skills/conversation", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  catalog: () => request<StudioCapabilityCatalogRecord>("catalog"),
  catalogImpact: (resourceType: StudioCatalogImpact["resourceType"], resourceId: string) =>
    request<StudioCatalogImpact>(
      `catalog/${resourceType}/${encodeURIComponent(resourceId)}/impact`,
    ),
  discoverMcp: (body: {
    reference: string;
    serverName: string;
    endpointUrl: string;
    networkAccess: "internal" | "external";
    customHeaders: Record<string, string>;
    authMode: "none" | "bearer" | "header" | "query";
    authName: string | null;
    authKey: string;
    credentialValue?: string;
  }) =>
    request<StudioMcpDiscoveryResult>("mcp/discover", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listMcpCredentials: () =>
    request<StudioMcpCredentialStatus[]>("mcp/credentials"),
  configureMcpCredential: (
    reference: string,
    authKey: string,
    value: string,
  ) => request<StudioMcpCredentialStatus>(
    `mcp/${encodeURIComponent(reference)}/credentials`,
    {
      method: "PUT",
      body: JSON.stringify({ authKey, value }),
    },
  ),
  deleteMcpCredential: (reference: string) =>
    request<StudioMcpCredentialStatus>(
      `mcp/${encodeURIComponent(reference)}/credentials`,
      { method: "DELETE" },
    ),
  upsertMcp: (
    reference: string,
    expectedRevision: number,
    resource: StudioCapabilities["mcpServers"][number],
    allowedExecutionProfileIds?: string[],
  ) =>
    request<StudioCatalogMutationResult>(
      `catalog/mcp/${encodeURIComponent(reference)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          expectedRevision,
          resource,
          ...(allowedExecutionProfileIds
            ? { allowedExecutionProfileIds }
            : {}),
        }),
      },
    ),
  disableMcp: (reference: string, expectedRevision: number) =>
    request<StudioCatalogMutationResult>(
      `catalog/mcp/${encodeURIComponent(reference)}?expected_revision=${expectedRevision}`,
      { method: "DELETE" },
    ),
  deleteMcp: (reference: string, expectedRevision: number) =>
    request<StudioCatalogMutationResult>(
      `catalog/mcp/${encodeURIComponent(reference)}/permanent?expected_revision=${expectedRevision}`,
      { method: "DELETE" },
    ),
  createDraft: (draft: Pick<StudioDraft, "name" | "domain" | "displayName" | "description" | "template">) =>
    request<ApiAgentDraft>("drafts", {
      method: "POST",
      body: JSON.stringify({
        name: draft.name,
        domain: draft.domain,
        displayName: draft.displayName,
        description: draft.description,
        template: draft.template,
      }),
    }).then(rememberStudioDraft),
  createDraftFromTask: async (body: {
    task: string;
    audience?: string;
    sampleInput?: string;
    runtimePreference: "auto" | StudioDraft["runtime"];
  }): Promise<StudioTaskDrivenDraftResult> => {
    try {
      const result = await request<StudioTaskDrivenDraftResult>("drafts/from-task", {
        method: "POST",
        body: JSON.stringify(body),
      });
      rememberStudioDraft(result.draft);
      return result;
    } catch (error) {
      if (!(error instanceof StudioApiError) || ![404, 405].includes(error.status)) {
        throw error;
      }

      // Older control-plane deployments do not expose /drafts/from-task yet.
      // Keep task-first creation available by falling back to the stable draft
      // endpoint; the Builder can still refine and run the resulting draft.
      const task = body.task.trim();
      const firstLine = task.split(/\r?\n/, 1)[0]?.trim() || "新智能体";
      const displayName = firstLine.length > 28
        ? `${firstLine.slice(0, 27)}…`
        : firstLine;
      const draft = await request<ApiAgentDraft>("drafts", {
        method: "POST",
        body: JSON.stringify({
          name: `task-agent-${createRandomId().slice(0, 8)}`,
          domain: "general",
          displayName,
          description: task,
          template: "analyst",
        }),
      });
      rememberStudioDraft(draft);
      return { draft, recommendation: null };
    }
  },
  readBuilderMaterials: (inputArtifactIds: string[], modelRoute?: string): Promise<{context: string}> => request("builder-materials", {method: "POST", body: JSON.stringify({inputArtifactIds, modelRoute})}),
  converseBuilder: (draftId: string, body: {
    expectedRevision: number;
    messages: { role: "user" | "assistant"; content: string }[];
    runContext: string;
    intent?: "auto" | "edit";
  }): Promise<StudioBuilderReply> => request(`drafts/${encodeURIComponent(draftId)}/builder-conversation`, {
    method: "POST", body: JSON.stringify(body),
  }),
  applyBuilderEdit: async (draftId: string, body: {
    expectedRevision: number; changes: StudioBuilderChanges;
  }): Promise<ApiAgentDraft> => {
    const draft = await request<ApiAgentDraft>(`drafts/${encodeURIComponent(draftId)}/builder-apply`, {
      method: "POST", body: JSON.stringify(body),
    });
    rememberStudioDraft(draft);
    return draft;
  },
  createAgentBuilderPatch: async (
    draftId: string,
    body: {
      expectedRevision: number;
      goal: string;
      audience?: string;
      inputs: string[];
      outputs: string[];
      constraints?: string[];
    },
  ): Promise<StudioAgentBuilderPatch> => {
    const raw = await request<{
      baseRevision: number;
      taskContract: NonNullable<StudioDraft["taskContract"]>;
      systemPrompt: string;
      evaluationCases: Array<{
        id: string;
        tags: string[];
        prompt: string;
        expect: StudioEvalCase["expect"];
      }>;
      explanation: string[];
      validation: StudioValidation;
    }>(`drafts/${encodeURIComponent(draftId)}/builder-patch`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return {
      baseRevision: raw.baseRevision,
      taskContract: raw.taskContract,
      systemPrompt: raw.systemPrompt,
      evaluationCases: raw.evaluationCases.map((item) => ({
        id: item.id,
        label: item.id,
        tag: (item.tags.find((tag) => ["happy", "ambiguous", "safety"].includes(tag)) ?? "happy") as StudioEvalCase["tag"],
        prompt: item.prompt,
        expect: item.expect,
      })),
      explanation: raw.explanation,
      validation: raw.validation,
    };
  },
  async importBundle(file: Blob): Promise<StudioImportedAgentBundle> {
    const response = requireAuthenticatedResponse(
      await fetch("/api/studio/drafts/import", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": (file instanceof File && file.name.toLowerCase().endsWith(".rar")) ? "application/vnd.rar" : "application/zip" },
        body: file,
      }),
    );
    if (!response.ok) throw await errorFrom(response);
    const imported = await response.json() as StudioImportedAgentBundle;
    rememberStudioDraft(imported.draft);
    return imported;
  },
  async importSkill(file: File): Promise<StudioImportedSkill> {
    const markdown = file.name.toLowerCase().endsWith(".md");
    const response = requireAuthenticatedResponse(
      await fetch(
        `/api/studio/skills/import?filename=${encodeURIComponent(file.name)}`,
        {
          method: "POST",
          cache: "no-store",
          headers: {
            "Content-Type": markdown ? "text/markdown" : "application/zip",
          },
          body: file,
        },
      ),
    );
    if (!response.ok) throw await errorFrom(response);
    return response.json() as Promise<StudioImportedSkill>;
  },
  listPlatformSkills: () =>
    request<StudioPlatformSkillCatalog>("skills/catalog"),
  async installPlatformSkill(
    draftId: string,
    expectedRevision: number,
    packageId: string,
    packageRevision: number,
  ): Promise<StudioInstalledSkill> {
    const installed = await request<StudioInstalledSkill>(
      `drafts/${encodeURIComponent(draftId)}/skills/catalog/`
        + `${encodeURIComponent(packageId)}/install`,
      {
        method: "POST",
        body: JSON.stringify({ expectedRevision, packageRevision }),
      },
    );
    rememberStudioDraft(installed.draft);
    return installed;
  },
  async installSkill(
    draftId: string,
    expectedRevision: number,
    file: File,
  ): Promise<StudioInstalledSkill> {
    const markdown = file.name.toLowerCase().endsWith(".md");
    const response = requireAuthenticatedResponse(
      await fetch(
        `/api/studio/drafts/${encodeURIComponent(draftId)}/skills/import`
          + `?filename=${encodeURIComponent(file.name)}`
          + `&expectedRevision=${expectedRevision}`,
        {
          method: "POST",
          cache: "no-store",
          headers: {
            "Content-Type": markdown ? "text/markdown" : "application/zip",
          },
          body: file,
        },
      ),
    );
    if (!response.ok) throw await errorFrom(response);
    const installed = await response.json() as StudioInstalledSkill;
    rememberStudioDraft(installed.draft);
    return installed;
  },
  replaceDraft: (draft: StudioDraft) =>
    request<ApiAgentDraft>(`drafts/${encodeURIComponent(draft.id)}`, {
      method: "PUT",
      body: JSON.stringify({
        expectedRevision: draft.revision,
        spec: studioDraftToSpec(draft),
      }),
    }).then(rememberStudioDraft),
  async deleteDraft(draftId: string, expectedRevision: number): Promise<void> {
    const response = requireAuthenticatedResponse(
      await fetch(
        `/api/studio/drafts/${encodeURIComponent(draftId)}`
          + `?expectedRevision=${expectedRevision}`,
        { method: "DELETE", cache: "no-store" },
      ),
    );
    if (!response.ok) throw await errorFrom(response);
    forgetStudioDraft(draftId);
  },
  validateDraft: (draftId: string) =>
    request<StudioValidation>(`drafts/${encodeURIComponent(draftId)}/validate`, {
      method: "POST",
    }),
  createTryRun: (
    draftId: string,
    expectedRevision: number,
    prompt: string,
    idempotencyKey: string,
    options: { continueFromRunId?: string; inputArtifactIds?: string[] } = {},
  ) => request<StudioTryRun>(`drafts/${encodeURIComponent(draftId)}/try-runs`, {
    method: "POST",
    body: JSON.stringify({ expectedRevision, prompt, idempotencyKey, ...options }),
  }),
  getTryRun: (draftId: string, draftRevision: number, runId: string) =>
    request<StudioTryRun>(
      `drafts/${encodeURIComponent(draftId)}/try-runs/${encodeURIComponent(runId)}`
        + `?draftRevision=${draftRevision}`,
    ),
  async streamTryRunEvents(
    draftId: string,
    draftRevision: number,
    runId: string,
    afterSequence: number,
    onEvent: (event: StudioTryRun["events"][number]) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const headers = new Headers({ Accept: "text/event-stream" });
    if (afterSequence > 0) headers.set("Last-Event-ID", String(afterSequence));
    const response = requireAuthenticatedResponse(await fetch(
      `/api/studio/drafts/${encodeURIComponent(draftId)}`
        + `/try-runs/${encodeURIComponent(runId)}/events`
        + `?draftRevision=${draftRevision}`,
      { headers, cache: "no-store", signal },
    ));
    await streamAgui(response, (_id, event) => {
      if (
        typeof event.event_id === "string"
        && typeof event.sequence === "number"
        && typeof event.type === "string"
        && typeof event.timestamp === "string"
        && event.payload
        && typeof event.payload === "object"
      ) {
        onEvent(event as StudioTryRun["events"][number]);
      }
    });
  },
  solidifyTryRun: (
    draftId: string,
    expectedRevision: number,
    draftRevision: number,
    runId: string,
  ) => request<StudioSolidifiedAgentResult>(
    `drafts/${encodeURIComponent(draftId)}/solidify`,
    {
      method: "POST",
      body: JSON.stringify({ expectedRevision, draftRevision, runId }),
    },
  ).then((result) => {
    rememberStudioDraft(result.draft);
    return result;
  }),
  decideTryRunApproval: (approvalId: string, decision: "approved" | "rejected") =>
    harnessRequest(`approvals/${encodeURIComponent(approvalId)}`, {
      method: "PUT",
      body: JSON.stringify({ decision }),
    }),
  cancelTryRun: (runId: string) =>
    harnessRequest(`runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),
  tryRunArtifactHref: (artifactId: string) =>
    `/api/harness/artifacts/${encodeURIComponent(artifactId)}/content`,
  publishDraft: (draftId: string, expectedRevision: number) =>
    request<ApiAgentVersion>(`drafts/${encodeURIComponent(draftId)}/publish`, {
      method: "POST",
      body: JSON.stringify({ expectedRevision }),
    }).then((version) => {
      forgetStudioDraft(draftId);
      return version;
    }),
  listPersonalAgentVersions: (agentId: string) =>
    agentRequest<PersonalAgentVersion[]>(`${encodeURIComponent(agentId)}/versions`),
  promotePersonalAgentVersion: (agentId: string, version: string) =>
    agentRequest<PersonalAgentVersion>(
      `${encodeURIComponent(agentId)}/versions/${encodeURIComponent(version)}/promote`,
      { method: "POST" },
    ),
  listPreviews: () => request<StudioPreview[]>("previews"),
  createPreview: (
    draftId: string,
    expectedRevision: number,
    idempotencyKey: string,
  ) => request<StudioPreview>("previews", {
    method: "POST",
    body: JSON.stringify({
      draftId,
      expectedRevision,
      idempotencyKey,
      ttlSeconds: 3600,
    }),
  }),
  getPreview: (previewId: string) =>
    request<StudioPreview>(`previews/${encodeURIComponent(previewId)}`),
  cancelPreview: (previewId: string) =>
    request<StudioPreview>(`previews/${encodeURIComponent(previewId)}/cancel`, {
      method: "POST",
    }),
  listEvalDatasets: () => request<StudioEvalDataset[]>("eval-datasets"),
  createEvalDataset: (
    draftId: string,
    expectedRevision: number,
    name: string,
    datasetId?: string,
  ) => request<StudioEvalDataset>("eval-datasets", {
    method: "POST",
    body: JSON.stringify({
      draftId,
      expectedRevision,
      name,
      ...(datasetId ? { datasetId } : {}),
      required: true,
    }),
  }),
  importEvalDataset: (
    draftId: string,
    expectedRevision: number,
    name: string,
    format: "json" | "csv",
    content: string,
  ) => request<StudioEvalDataset>("eval-datasets/import", {
    method: "POST",
    body: JSON.stringify({
      draftId,
      expectedRevision,
      name,
      required: true,
      format,
      content,
    }),
  }),
  listEvalRuns: () => request<StudioEvalRun[]>("eval-runs"),
  createEvalRun: (
    dataset: StudioEvalDataset,
    agentVersion: string,
    idempotencyKey: string,
    previewId?: string,
  ) => request<StudioEvalRun>("eval-runs", {
    method: "POST",
    body: JSON.stringify({
      datasetId: dataset.datasetId,
      datasetVersion: dataset.version,
      agentName: dataset.agentName,
      agentVersion,
      idempotencyKey,
      ...(previewId ? { previewId } : {}),
    }),
  }),
  getEvalRun: (evalRunId: string) =>
    request<StudioEvalRun>(`eval-runs/${encodeURIComponent(evalRunId)}`),
  cancelEvalRun: (evalRunId: string) =>
    request<StudioEvalRun>(`eval-runs/${encodeURIComponent(evalRunId)}/cancel`, {
      method: "POST",
    }),
  getEvalGate: (agentName: string, agentVersion: string) =>
    request<StudioEvalGate>(
      `evaluation-gates/${encodeURIComponent(agentName)}/versions/${encodeURIComponent(agentVersion)}`,
    ),
  listEnvironments: (agentName: string) =>
    request<StudioEnvironment[]>(
      `agents/${encodeURIComponent(agentName)}/environments`,
    ),
  replaceEnvironmentPolicy: async (
    agentName: string,
    environment: StudioEnvironment,
    policy: StudioEnvironmentResourcePolicy,
  ) => {
    const catalog = await request<StudioCapabilityCatalogRecord>("catalog");
    return request<StudioEnvironment>(
      `agents/${encodeURIComponent(agentName)}/environments/${environment.name}/policy`,
      {
        method: "PUT",
        body: JSON.stringify({
          expectedEnvironmentRevision: environment.revision,
          policy: {
            ...policy,
            capabilityCatalogRevision: catalog.revision,
          },
        }),
      },
    );
  },
  listTriggers: (agentName: string) =>
    request<StudioAgentTrigger[]>(
      `agents/${encodeURIComponent(agentName)}/triggers`,
    ),
  createTrigger: (
    agentName: string,
    input: string | {
      name: string;
      environment: StudioEnvironmentName;
      kind: StudioAgentTrigger["kind"];
      schedule?: StudioAgentTrigger["schedule"];
      chatops?: StudioAgentTrigger["chatops"];
    },
    legacyEnvironment?: StudioEnvironmentName,
  ) => request<StudioCreatedAgentTrigger>(
    `agents/${encodeURIComponent(agentName)}/triggers`,
    {
      method: "POST",
      body: JSON.stringify(
        typeof input === "string"
          ? { name: input, environment: legacyEnvironment }
          : input,
      ),
    },
  ),
  updateTrigger: (
    trigger: StudioAgentTrigger,
    update: Pick<StudioAgentTrigger, "name" | "enabled">,
  ) => request<StudioAgentTrigger>(
    `triggers/${encodeURIComponent(trigger.triggerId)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        expectedRevision: trigger.revision,
        name: update.name,
        enabled: update.enabled,
      }),
    },
  ),
  rotateTriggerSecret: (trigger: StudioAgentTrigger) =>
    request<StudioCreatedAgentTrigger>(
      `triggers/${encodeURIComponent(trigger.triggerId)}/rotate-secret`,
      {
        method: "POST",
        body: JSON.stringify({ expectedRevision: trigger.revision }),
      },
    ),
  listDeployments: (agentName: string) =>
    request<StudioDeployment[]>(
      `agents/${encodeURIComponent(agentName)}/deployments`,
    ),
  listDeploymentSnapshots: (agentName: string) =>
    request<StudioDeploymentSnapshot[]>(
      `agents/${encodeURIComponent(agentName)}/deployment-snapshots`,
    ),
  promoteDeployment: (
    agentName: string,
    agentVersion: string,
    environment: StudioEnvironment,
    packageHash: string,
    executionProfile: string,
    canaryPercent: number,
  ) => request<StudioDeployment>("deployments/promote", {
    method: "POST",
    body: JSON.stringify({
      agentName,
      agentVersion,
      environment: environment.name,
      expectedEnvironmentRevision: environment.revision,
      canaryPercent,
      imageDigest: `sha256:${packageHash}`,
      executionProfile,
      config: {},
      idempotencyKey: `studio-deploy:${agentName}:${agentVersion}:${environment.name}:r${environment.revision}:${createRandomId()}`,
    }),
  }),
  rollbackDeployment: (
    agentName: string,
    environment: StudioEnvironment,
    snapshotId: string,
  ) => request<StudioDeployment>(
    `agents/${encodeURIComponent(agentName)}/environments/${environment.name}/rollback`,
    {
      method: "POST",
      body: JSON.stringify({
        snapshotId,
        expectedEnvironmentRevision: environment.revision,
        idempotencyKey: `studio-rollback:${agentName}:${environment.name}:${snapshotId}:r${environment.revision}:${createRandomId()}`,
      }),
    },
  ),
  listQualityScores: (agentName: string) =>
    request<StudioQualityScore[]>(
      `agents/${encodeURIComponent(agentName)}/quality/scores`,
    ),
  listQualityIncidents: (agentName: string) =>
    request<StudioQualityIncident[]>(
      `agents/${encodeURIComponent(agentName)}/quality/incidents`,
    ),
  listQualityRules: (agentName: string) =>
    request<StudioQualityRule[]>(
      `agents/${encodeURIComponent(agentName)}/quality/rules`,
    ),
  getQualityGate: (agentName: string, agentVersion: string) =>
    request<StudioQualityGate>(
      `agents/${encodeURIComponent(agentName)}/versions/${encodeURIComponent(agentVersion)}/quality-gate`,
    ),
  async downloadEvalArtifact(evalRunId: string, artifactId: string): Promise<void> {
    const response = requireAuthenticatedResponse(
      await fetch(
        `/api/studio/eval-runs/${encodeURIComponent(evalRunId)}/artifacts/${encodeURIComponent(artifactId)}`,
        { cache: "no-store" },
      ),
    );
    if (!response.ok) throw await errorFrom(response);
    const disposition = response.headers.get("content-disposition") ?? "";
    const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "eval-report";
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  },
  async downloadBundle(draftId: string): Promise<void> {
    const response = requireAuthenticatedResponse(
      await fetch(`/api/studio/drafts/${encodeURIComponent(draftId)}/bundle`, {
        cache: "no-store",
      }),
    );
    if (!response.ok) throw await errorFrom(response);
    const disposition = response.headers.get("content-disposition") ?? "";
    const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "agent-bundle.zip";
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  },
  async downloadNexauBundle(draftId: string): Promise<void> {
    const response = requireAuthenticatedResponse(
      await fetch(`/api/studio/drafts/${encodeURIComponent(draftId)}/nexau-bundle`, {
        cache: "no-store",
      }),
    );
    if (!response.ok) throw await errorFrom(response);
    const disposition = response.headers.get("content-disposition") ?? "";
    const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "nexau-agent.zip";
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  },
};

export function capabilityOptions(catalog: StudioCapabilities): {
  routes: ModelRouteOption[];
  tools: BuiltinToolOption[];
  mcp: McpOption[];
  profiles: StudioCapabilities["executionProfiles"];
  templates: StudioCapabilities["templates"];
  runtimes: StudioCapabilities["runtimeCapabilities"];
} {
  return {
    routes: catalog.modelRoutes.filter(
      (item) =>
        item.enabled &&
        (item.modelType === undefined || ["chat", "vision"].includes(item.modelType)) &&
        item.apiFormat !== "openai_images" &&
        item.apiFormat !== "openai_videos" &&
        !item.capabilities.includes("image_generation") &&
        !item.capabilities.includes("video_generation"),
    ).map((item) => ({
      id: item.routeId,
      label: item.label,
      provider: item.provider,
      models: item.models,
      capabilities: item.capabilities,
      apiFormat: item.apiFormat === "openai_videos" ? undefined : item.apiFormat,
    })),
    tools: catalog.builtinTools.map((item) => ({
      id: item.name,
      label: item.label,
      description: item.description,
      risk: item.risk,
      approval: item.approvalBehavior,
    })),
    mcp: catalog.mcpServers.filter((item) => item.enabled).map((item) => ({
      id: item.reference,
      category: item.category,
      label: item.label,
      description: item.description,
      tools: item.tools,
      network: item.networkAccess,
      sendsUserData: item.sendsUserData,
    })),
    profiles: catalog.executionProfiles.filter((item) => item.enabled),
    templates: catalog.templates ?? [],
    runtimes: catalog.runtimeCapabilities ?? [],
  };
}
