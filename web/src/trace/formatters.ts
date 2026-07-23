const PREFERRED_ARGUMENT_KEYS = [
  "action",
  "url",
  "path",
  "command",
  "query",
  "method",
  "selector",
  "name",
  "skill",
  "memory",
  "context_id",
] as const;

const SECRET_KEYS = new Set([
  "api_key",
  "token",
  "password",
  "authorization",
  "secret",
  "key",
]);

export function formatToolName(value: unknown): string {
  if (typeof value !== "string") return "tool";
  const name = value.trim();
  return name || "tool";
}

/** Keep byte parity with agentharness.tools.summary.summarize_tool_arguments. */
export function summarizeArgs(argumentsValue: unknown, maxLen = 160): string {
  if (argumentsValue == null) return "";
  if (typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) {
    return truncate(String(argumentsValue), maxLen);
  }

  const args = argumentsValue as Record<string, unknown>;
  const parts: string[] = [];
  for (const key of PREFERRED_ARGUMENT_KEYS) {
    if (args[key] == null || args[key] === "") continue;
    const value =
      typeof args[key] === "string" ? truncate(args[key] as string, 80) : String(args[key]);
    parts.push(`${key}=${value}`);
  }

  if (!parts.length) {
    for (const [key, value] of Object.entries(args).slice(0, 4)) {
      const normalizedKey = key.toLowerCase();
      if (SECRET_KEYS.has(normalizedKey) || normalizedKey.includes("token")) {
        parts.push(`${key}=[REDACTED]`);
        continue;
      }
      const rendered = typeof value === "string" ? truncate(value, 60) : String(value);
      parts.push(`${key}=${rendered}`);
    }
  }

  return truncate(parts.join(" "), maxLen);
}

export function formatOutputPreview(value: unknown, maxLen = 180): string {
  if (value == null || value === "") return "无输出";
  let rendered: string;
  if (typeof value === "string") {
    rendered = value;
  } else {
    try {
      rendered = JSON.stringify(value);
    } catch {
      rendered = String(value);
    }
  }
  const compact = rendered.replace(/\s+/g, " ").trim();
  return truncate(compact || "无输出", maxLen);
}

export function formatPayloadPreview(payload: Record<string, unknown>, maxLen = 180): string {
  if (typeof payload.text === "string") return formatOutputPreview(payload.text, maxLen);
  if (typeof payload.content_preview === "string") {
    return formatOutputPreview(payload.content_preview, maxLen);
  }
  if (typeof payload.arguments_summary === "string") {
    return truncate(payload.arguments_summary, maxLen);
  }
  if (payload.arguments != null) return summarizeArgs(payload.arguments, maxLen);
  if (typeof payload.name === "string") return formatToolName(payload.name);
  if (typeof payload.tool === "string") return formatToolName(payload.tool);
  if (typeof payload.error === "string") return formatOutputPreview(payload.error, maxLen);
  if (typeof payload.status === "string") return payload.status;
  if (typeof payload.phase === "string") {
    return `phase=${payload.phase} · step=${String(payload.step ?? "-")}`;
  }
  return Object.keys(payload).length ? formatOutputPreview(payload, maxLen) : "无载荷";
}

function truncate(value: string, maxLen: number): string {
  return value.length <= maxLen ? value : `${value.slice(0, Math.max(0, maxLen - 1))}…`;
}
