import { describe, expect, it } from "vitest";

import schema from "../../../contracts/procurement-workbench.schema.json";
import failedExample from "../../../contracts/examples/ai-task-failed.json";
import staleExample from "../../../contracts/examples/ai-task-stale.json";
import {
  AI_STATUS_TRANSITIONS,
  AI_TASK_STATUSES,
  AI_TASK_STEPS,
  AI_TASK_TYPES,
  CONTRACT_STATUSES,
  type HumanInteractionStatus,
  INVOICE_STATUSES,
  PROCUREMENT_STATUSES,
  type AiTaskView,
} from "./types";

type ContractDefinition = { enum: string[] };

const HUMAN_INTERACTION_STATUSES: HumanInteractionStatus[] = [
  "WAITING", "ANSWERED", "APPLIED", "STALE", "EXPIRED", "CANCELLED",
];

describe("procurement workbench contract", () => {
  it("keeps Web status values aligned with the shared contract", () => {
    // JSON 模块的字面量类型与松散遍历的契约结构本就不重叠：显式两段转换。
    const definitions = schema.$defs as unknown as Record<string, ContractDefinition>;
    expect([...PROCUREMENT_STATUSES]).toEqual(definitions.ProcurementStatus.enum);
    expect([...AI_TASK_STATUSES]).toEqual(definitions.AiTaskStatus.enum);
    expect([...AI_TASK_TYPES]).toEqual(definitions.AiTaskType.enum);
    expect([...AI_TASK_STEPS]).toEqual(definitions.AiTaskStep.enum);
    expect([...INVOICE_STATUSES]).toEqual(definitions.InvoiceStatus.enum);
    expect([...CONTRACT_STATUSES]).toEqual(definitions.ContractStatus.enum);
    expect(HUMAN_INTERACTION_STATUSES).toEqual(definitions.HumanInteractionStatus.enum);
    expect(AI_STATUS_TRANSITIONS).toEqual(schema["x-ai-status-transitions"]);
  });

  it("publishes the complete human interaction and operation boundary", () => {
    const definitions = schema.$defs as Record<string, Record<string, unknown>>;
    expect(definitions.HumanInteractionView.required).toContain("answer_schema");
    expect(definitions.HumanInteractionView.required).toContain("operation_id");
    expect(definitions.HumanInteractionAnswerRequest.required).toEqual(["answer"]);
    expect(definitions.HumanInteractionArtifact.required).toContain("sha256");
    expect(definitions.ProcurementOperation.required).toContain("payload_sha256");
  });

  it("keeps missing draft quantity and unit as explicit unknown facts", () => {
    const definitions = schema.$defs as unknown as Record<string, { properties?: Record<string, { type?: string[] }> }>;
    expect(definitions.ProcurementRequirementView.properties?.quantity.type).toContain("null");
    expect(definitions.ProcurementRequirementView.properties?.unit.type).toContain("null");
  });

  it("represents failure and stale results independently", () => {
    const failed: AiTaskView = failedExample as AiTaskView;
    const stale: AiTaskView = staleExample as AiTaskView;
    expect(failed.status).toBe("FAILED");
    expect(failed.retryable).toBe(true);
    expect(failed.error_code).toBeTruthy();
    expect(stale.status).toBe("SUCCEEDED");
    expect(stale.stale).toBe(true);
    expect(stale.stale_reason).toBe("INPUT_GENERATION_CHANGED");
  });
});
