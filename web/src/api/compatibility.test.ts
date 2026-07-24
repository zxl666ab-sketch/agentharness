import { describe, expect, it } from "vitest";
import type { HealthResponse } from "./client";
import {
  checkBackendCompatibility,
  REQUIRED_API_SCHEMA_VERSION,
} from "./compatibility";

function health(overrides: Partial<HealthResponse> = {}): HealthResponse {
  return {
    service: "agentharness",
    status: "ok",
    data_dir: "/tmp/data",
    max_global_seq: 0,
    api_schema_version: REQUIRED_API_SCHEMA_VERSION,
    web_build_id: "web-current",
    ...overrides,
  };
}

describe("backend compatibility handshake", () => {
  it("rejects old servers that do not expose an API schema", () => {
    const result = checkBackendCompatibility(
      health({ api_schema_version: undefined }),
      null
    );
    expect(result).toMatchObject({
      compatible: false,
      reason: "api_schema",
      actual: "未提供",
    });
  });

  it("rejects a frontend that was rebuilt after the backend started", () => {
    const result = checkBackendCompatibility(health(), "web-new");
    expect(result).toMatchObject({
      compatible: false,
      reason: "web_build",
      expected: "web-new",
      actual: "web-current",
    });
  });

  it("accepts matching API and Web build identities", () => {
    expect(checkBackendCompatibility(health(), "web-current")).toEqual({
      compatible: true,
    });
  });
});
