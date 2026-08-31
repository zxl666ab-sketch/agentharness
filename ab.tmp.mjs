/* Capture A/B: same requirement message twice.
 * Cold run = model capture (hy3 turns). Warm run = cache pre-routing (zero remote turns).
 * Metrics: remote model turns + tokens per run (from /events), wall clock to operation completion. */
const BASE = "http://127.0.0.1:8741";
const MESSAGE =
  "采购 PE 快递袋 10,000 个，尺寸 250mm×350mm，厚度 60 微米，白色，单色印刷，10 天内送达，需要开票";
const REMOTE_MODELS = new Set(["hy3", "deepseek-v4-flash", "gpt-5.4"]);

async function json(path, init) {
  const response = await fetch(BASE + path, init);
  const text = await response.text();
  if (!response.ok) throw new Error(`${path} -> ${response.status}: ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
}

async function once(label) {
  const form = new FormData();
  form.append("message", MESSAGE);
  const started = Date.now();
  const accepted = await json("/api/procurement/conversations", {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: form,
  });
  const operationId = accepted.operation_id;
  const taskId = accepted.purchase_request_id;
  let operation = null;
  while (Date.now() - started < 90_000) {
    operation = await json(`/api/procurement/operations/${operationId}`);
    if (["completed", "failed", "cancelled"].includes(operation.status)) break;
    await new Promise((r) => setTimeout(r, 400));
  }
  const wallMs = Date.now() - started;
  const view = await json(`/api/procurement/requests/${taskId}`);
  const runId = view.analysis_run_id;
  let remoteTurns = 0;
  let remoteTokens = 0;
  let localTurns = 0;
  if (runId) {
    const events = await json(`/api/runs/${runId}/events`);
    for (const event of events) {
      if (event.type !== "model_turn_end") continue;
      const model = event.payload?.model ?? "";
      const usage = event.payload?.usage ?? {};
      if (REMOTE_MODELS.has(model)) {
        remoteTurns += 1;
        remoteTokens += (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0);
      } else {
        localTurns += 1;
      }
    }
  }
  const out = {
    label,
    task: taskId.slice(0, 8),
    op_status: operation?.status,
    wall_ms: wallMs,
    remote_turns: remoteTurns,
    remote_tokens: remoteTokens,
    local_turns: localTurns,
  };
  console.log(JSON.stringify(out));
  return out;
}

const cold = await once("COLD");
await new Promise((r) => setTimeout(r, 1500));
const warm = await once("WARM");
console.log(
  "AB_RESULT",
  JSON.stringify({
    remote_turns: `${cold.remote_turns}->${warm.remote_turns}`,
    remote_tokens: `${cold.remote_tokens}->${warm.remote_tokens}`,
    token_reduction_pct:
      cold.remote_tokens > 0
        ? Number((((cold.remote_tokens - warm.remote_tokens) / cold.remote_tokens) * 100).toFixed(1))
        : null,
    wall_ms: `${cold.wall_ms}->${warm.wall_ms}`,
    wall_saved_ms: cold.wall_ms - warm.wall_ms,
  }),
);
