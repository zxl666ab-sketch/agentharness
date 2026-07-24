import type { HealthResponse } from "./client";

export const REQUIRED_API_SCHEMA_VERSION = __AGENTHARNESS_API_SCHEMA_VERSION__;
export const WEB_BUILD_ID = __AGENTHARNESS_WEB_BUILD_ID__;

export type BackendCompatibility =
  | { compatible: true }
  | {
      compatible: false;
      reason: "api_schema" | "web_build";
      expected: string;
      actual: string;
    };

export function checkBackendCompatibility(
  health: HealthResponse,
  expectedWebBuildId = __AGENTHARNESS_ENFORCE_WEB_BUILD_ID__ ? WEB_BUILD_ID : null
): BackendCompatibility {
  if (health.api_schema_version !== REQUIRED_API_SCHEMA_VERSION) {
    return {
      compatible: false,
      reason: "api_schema",
      expected: String(REQUIRED_API_SCHEMA_VERSION),
      actual: health.api_schema_version == null ? "未提供" : String(health.api_schema_version),
    };
  }
  if (expectedWebBuildId && health.web_build_id !== expectedWebBuildId) {
    return {
      compatible: false,
      reason: "web_build",
      expected: expectedWebBuildId,
      actual: health.web_build_id || "未提供",
    };
  }
  return { compatible: true };
}
