import { describe, expect, it } from "vitest";

import schema from "../../../contracts/procurement-workbench.schema.json";
import failedExample from "../../../contracts/examples/ai-task-failed.json";
import staleExample from "../../../contracts/examples/ai-task-stale.json";
import {
  AI_STATUS_TRANSITIONS,
  AI_TASK_STATUSES,
  AI_TASK_STEPS,
  AI_TASK_TYPES,
  PROCUREMENT_STATUSES,
  type AiTaskView,
} from "./types";

type ContractDefinition = { enum: string[] };

describe("procurement workbench contract", () => {
  it("keeps Web status values aligned with the shared contract", () => {
    const definitions = schema.$defs as Record<string, ContractDefinition>;
    expect([...PROCUREMENT_STATUSES]).toEqual(definitions.ProcurementStatus.enum);
    expect([...AI_TASK_STATUSES]).toEqual(definitions.AiTaskStatus.enum);
    expect([...AI_TASK_TYPES]).toEqual(definitions.AiTaskType.enum);
    expect([...AI_TASK_STEPS]).toEqual(definitions.AiTaskStep.enum);
    expect(AI_STATUS_TRANSITIONS).toEqual(schema["x-ai-status-transitions"]);
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
