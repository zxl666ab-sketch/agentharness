const EFFECT_GLYPHS: Record<string, { glyph: string; label: string }> = {
  pure: { glyph: "纯", label: "纯函数：无副作用" },
  destructive: { glyph: "危", label: "高风险操作" },
};

export function effectClass(effect: string): string {
  if (effect === "destructive") return "danger";
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
