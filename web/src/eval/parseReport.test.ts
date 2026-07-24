import { describe, expect, it } from "vitest";
import {
  compareReports,
  filterFailed,
  parseEvalReport,
  type EvalReport,
} from "./parseReport";

const sample = JSON.stringify({
  schema_version: 1,
  suite: "smoke",
  total: 2,
  passed: 1,
  pass_rate: 0.5,
  mean_score: 0.5,
  total_tokens: 12,
  mean_latency_s: 0.2,
  results: [
    {
      case_id: "ok",
      run_id: "run-ok",
      status: "completed",
      passed: true,
      score: 1,
      latency_s: 0.1,
      total_tokens: 5,
      steps: 1,
      reasons: [],
    },
    {
      case_id: "bad",
      run_id: "run-bad",
      status: "failed",
      passed: false,
      score: 0,
      latency_s: 0.3,
      total_tokens: 7,
      steps: 2,
      reasons: ["missing substring: x"],
    },
  ],
});

describe("parseEvalReport", () => {
  it("parses a valid smoke JSON report", () => {
    const result = parseEvalReport(sample);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.report.suite).toBe("smoke");
    expect(result.report.results).toHaveLength(2);
    expect(result.report.results[1].reasons[0]).toContain("missing");
  });

  it("rejects empty input", () => {
    const result = parseEvalReport("");
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error).toMatch(/empty/i);
  });

  it("rejects invalid JSON", () => {
    const result = parseEvalReport("{not json");
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error).toMatch(/json/i);
  });

  it("rejects missing results", () => {
    const result = parseEvalReport(JSON.stringify({ schema_version: 1 }));
    expect(result.ok).toBe(false);
  });

  it("rejects unknown schema", () => {
    const result = parseEvalReport(
      JSON.stringify({ schema_version: 99, results: [] })
    );
    expect(result.ok).toBe(false);
  });
});

describe("filterFailed", () => {
  it("filters to failed cases only", () => {
    const parsed = parseEvalReport(sample);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const failed = filterFailed(parsed.report.results, true);
    expect(failed).toHaveLength(1);
    expect(failed[0].case_id).toBe("bad");
    expect(filterFailed(parsed.report.results, false)).toHaveLength(2);
  });
});

describe("compareReports", () => {
  it("detects new failures and score drops", () => {
    const cur = parseEvalReport(sample);
    expect(cur.ok).toBe(true);
    if (!cur.ok) return;
    const baseline: EvalReport = {
      ...cur.report,
      passed: 2,
      pass_rate: 1,
      mean_score: 1,
      results: cur.report.results.map((r) => ({
        ...r,
        passed: true,
        score: 1,
        reasons: [],
      })),
    };
    const cmp = compareReports(cur.report, baseline);
    expect(cmp.new_failures).toContain("bad");
    expect(cmp.score_drops.some((d) => d.case_id === "bad")).toBe(true);
  });
});
