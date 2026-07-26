const EFFECT_GLYPHS: Record<string, { glyph: string; label: string }> = {
  pure: { glyph: "纯", label: "纯函数：无副作用" },
  workspace_read: { glyph: "读", label: "读取工作区" },
  workspace_write: { glyph: "写", label: "写入工作区" },
  network: { glyph: "网", label: "访问网络" },
  external_write: { glyph: "外", label: "外部写入" },
  destructive: { glyph: "危", label: "高风险操作" },
};

export function effectClass(effect: string): string {
  if (effect === "workspace_write") return "write";
  if (effect === "network") return "network";
  if (effect === "destructive") return "danger";
  if (effect === "external_write") return "external";
  return "read";
}

export function EffectBadge({ effect }: { effect: string }) {
  const meta = EFFECT_GLYPHS[effect] || { glyph: "?", label: effect };
  return (
    <span
      className={`effect-badge ${effectClass(effect)}`}
      title={`${meta.label}（${effect}）`}
      aria-label={meta.label}
    >
      {meta.glyph}
    </span>
  );
}
