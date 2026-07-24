import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ContextManifestRow, RunRow } from "../api/client";
import { ContextInspector } from "./ContextInspector";

describe("ContextInspector manifests", () => {
  it("shows real per-turn sources, budget, reasons, compression and fingerprint", () => {
    const run: RunRow = {
      id: "run-context",
      session_id: "session",
      root_run_id: "run-context",
      status: "completed",
      created_at: "2026-07-24T00:00:00Z",
      updated_at: "2026-07-24T00:00:01Z",
    };
    const manifest: ContextManifestRow = {
      schema_version: 1,
      run_id: run.id,
      model_turn: 0,
      budget_tokens: 1000,
      total_tokens: 420,
      token_method: "estimate",
      prefix_fingerprint: "abc123stablefingerprint",
      compacted: true,
      created_at: "2026-07-24T00:00:00Z",
      artifact_id: "artifact-context-manifest",
      items: [
        {
          section: "system",
          source: "harness.default_system",
          content_hash: "hash-system",
          token_estimate: 100,
          included: true,
          reason: "required safety and run instructions",
          compression: "none",
        },
        {
          section: "workspace_rules",
          source: "D:/workspace/AGENTS.md",
          content_hash: "hash-rules",
          token_estimate: 80,
          included: true,
          reason: "workspace rule from root-to-cwd hierarchy",
          compression: "none",
        },
        {
          section: "messages",
          source: "message:old",
          content_hash: "hash-old",
          token_estimate: 240,
          included: false,
          reason: "excluded by priority: older conversation history",
          compression: "externalized",
          artifact_id: "artifact-old-message",
        },
      ],
    };

    const html = renderToString(
      <ContextInspector run={run} transcript={[]} contexts={[manifest]} />
    );

    expect(html).toContain('data-testid="context-manifest"');
    expect(html).toContain("420");
    expect(html).toContain("1,000");
    expect(html).toContain("令牌");
    expect(html).toContain("abc123stablefingerprint");
    expect(html).toContain("系统提示");
    expect(html).toContain("工作区规则");
    expect(html).toContain("技能");
    expect(html).toContain("记忆");
    expect(html).toContain("对话消息");
    expect(html).toContain("工具规范");
    expect(html).toContain("按优先级排除");
    expect(html).toContain("产物外置");
  });
});
