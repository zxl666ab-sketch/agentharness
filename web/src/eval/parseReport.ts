/** Parse and normalize offline eval JSON reports for the Eval view. */

export type EvalCaseRow = {
  case_id: string;
  logical_case_id?: string;
  run_id: string;
  status: string;
  passed: boolean;
  score: number;
  latency_s: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens: number;
  steps: number;
  reasons: string[];
  provider?: string;
  model?: string | null;
  tags?: string[];
};

export type EvalReport = {
  schema_version: number;
  suite: string;
  total: number;
  passed: number;
  pass_rate: number;
  mean_score: number;
  total_tokens: number;
  mean_latency_s: number;
  data_dir?: string | null;
  groups?: unknown[];
  results: EvalCaseRow[];
};

export type EvalParseResult =
  | { ok: true; report: EvalReport }
  | { ok: false; error: string };

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function bool(value: unknown): boolean {
  return value === true;
}

export function parseEvalReport(raw: string): EvalParseResult {
  if (!raw || !raw.trim()) {
    return { ok: false, error: "Empty report" };
  }
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return { ok: false, error: "Invalid JSON" };
  }
  const obj = asRecord(data);
  if (!obj) {
    return { ok: false, error: "Report must be a JSON object" };
  }
  const schema = obj.schema_version;
  if (schema !== 1 && schema !== "1") {
    return { ok: false, error: `Unsupported schema_version: ${String(schema)}` };
  }
  if (!Array.isArray(obj.results)) {
    return { ok: false, error: "Missing results[]" };
  }

  const results: EvalCaseRow[] = [];
  for (const item of obj.results) {
    const row = asRecord(item);
    if (!row) continue;
    const reasonsRaw = row.reasons;
    const reasons = Array.isArray(reasonsRaw)
      ? reasonsRaw.map((r) => String(r))
      : [];
    results.push({
      case_id: str(row.case_id, "?"),
      logical_case_id: typeof row.logical_case_id === "string" ? row.logical_case_id : undefined,
      run_id: str(row.run_id),
      status: str(row.status, "unknown"),
      passed: bool(row.passed),
      score: num(row.score),
      latency_s: num(row.latency_s),
      input_tokens: typeof row.input_tokens === "number" ? row.input_tokens : undefined,
      output_tokens: typeof row.output_tokens === "number" ? row.output_tokens : undefined,
      total_tokens: num(row.total_tokens),
      steps: num(row.steps),
      reasons,
      provider: typeof row.provider === "string" ? row.provider : undefined,
      model: (row.model as string | null | undefined) ?? null,
      tags: Array.isArray(row.tags) ? row.tags.map(String) : undefined,
    });
  }

  const total = num(obj.total, results.length);
  const passed = num(obj.passed, results.filter((r) => r.passed).length);
  const pass_rate =
    typeof obj.pass_rate === "number"
      ? obj.pass_rate
      : total > 0
        ? passed / total
        : 0;
  const mean_score =
    typeof obj.mean_score === "number"
      ? obj.mean_score
      : results.length
        ? results.reduce((s, r) => s + r.score, 0) / results.length
        : 0;
  const total_tokens =
    typeof obj.total_tokens === "number"
      ? obj.total_tokens
      : results.reduce((s, r) => s + r.total_tokens, 0);
  const mean_latency_s =
    typeof obj.mean_latency_s === "number"
      ? obj.mean_latency_s
      : results.length
        ? results.reduce((s, r) => s + r.latency_s, 0) / results.length
        : 0;

  return {
    ok: true,
    report: {
      schema_version: 1,
      suite: str(obj.suite, "suite"),
      total,
      passed,
      pass_rate,
      mean_score,
      total_tokens,
      mean_latency_s,
      data_dir: typeof obj.data_dir === "string" ? obj.data_dir : null,
      groups: Array.isArray(obj.groups) ? obj.groups : [],
      results,
    },
  };
}

export function filterFailed(rows: EvalCaseRow[], onlyFailed: boolean): EvalCaseRow[] {
  return onlyFailed ? rows.filter((r) => !r.passed) : rows;
}

/** Lightweight baseline vs current summary (mirrors baseline.py priorities). */
export type BaselineCompareSummary = {
  new_failures: string[];
  score_drops: { case_id: string; baseline: number; current: number; delta: number }[];
  token_delta: { baseline: number; current: number; ratio_increase: number };
  latency_delta: { baseline: number; current: number; ratio_increase: number };
  new_case_count: number;
};

export function compareReports(
  current: EvalReport,
  baseline: EvalReport
): BaselineCompareSummary {
  const baseIdx = new Map(baseline.results.map((r) => [r.case_id, r]));
  const curIdx = new Map(current.results.map((r) => [r.case_id, r]));
  const new_failures: string[] = [];
  const score_drops: BaselineCompareSummary["score_drops"] = [];

  for (const [cid, cur] of curIdx) {
    const base = baseIdx.get(cid);
    if (!base) continue;
    if (base.passed && !cur.passed) new_failures.push(cid);
    const drop = base.score - cur.score;
    if (drop > 0) {
      score_drops.push({
        case_id: cid,
        baseline: base.score,
        current: cur.score,
        delta: -drop,
      });
    }
  }

  const baseTok = baseline.total_tokens;
  const curTok = current.total_tokens;
  const baseLat = baseline.mean_latency_s;
  const curLat = current.mean_latency_s;

  return {
    new_failures,
    score_drops,
    token_delta: {
      baseline: baseTok,
      current: curTok,
      ratio_increase: baseTok > 0 ? curTok / baseTok - 1 : 0,
    },
    latency_delta: {
      baseline: baseLat,
      current: curLat,
      ratio_increase: baseLat > 0 ? curLat / baseLat - 1 : 0,
    },
    new_case_count: [...curIdx.keys()].filter((c) => !baseIdx.has(c)).length,
  };
}
