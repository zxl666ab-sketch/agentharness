import { describe, expect, it } from "vitest";
import type { HealthResponse } from "./client";
import {
  checkBackendCompatibility,
  REQUIRED_API_SCHEMA_VERSION,
} from "./compatibility";

function health(overrides: Partial<HealthResponse> = {}): HealthResponse {
  const base: HealthResponse = {
    service: "agentharness",
    status: "ok",
    backend_version: "0.5.0",
    api_capabilities: [],
    data_dir: "/tmp/data",
    max_global_seq: 0,
    api_schema_version: REQUIRED_API_SCHEMA_VERSION,
    web_build_id: "web-current",
  };
  // 旧后端会整体缺失 api_schema_version：显式允许 undefined 穿透到断言对象。
  return { ...base, ...overrides } as HealthResponse;
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
