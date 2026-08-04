import { describe, expect, it } from "vitest";

import { agentStreamUrl } from "./useAgentStream";

describe("agent stream cursor", () => {
  it("starts live without replaying the complete event history", () => {
    expect(agentStreamUrl(0)).toBe("/api/stream");
  });

  it("keeps an explicit positive cursor for replay and reconnect tests", () => {
    expect(agentStreamUrl(42)).toBe("/api/stream?after=42");
  });
});
