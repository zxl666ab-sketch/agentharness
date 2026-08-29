/**
 * 展示层格式化（Phase 3）：英文枚举中文化映射、空值统一"——"、
 * 金额/日期本地化。只影响展示，不触碰业务数据。
 */

export const DASH = "——";

/** 空值统一渲染为破折号，杜绝"未填写xxx"灰字噪音。 */
export function dashIfEmpty(value: string | number | null | undefined): string {
  if (value == null) return DASH;
  const text = typeof value === "number" ? String(value) : value.trim();
  if (text === "" || text === "-" || text === "未说明" || text === "未填写") return DASH;
  return text;
}

/** 采购品类中文化（未知品类回退原值，绝不把英文枚举当文案硬编码）。 */
export const CATEGORY_LABELS: Record<string, string> = {
  ecommerce_packaging: "电商包装",
  general: "通用物料",
  office_supplies: "办公用品",
  mro: "MRO 工业品",
  raw_materials: "原材料",
  logistics: "物流服务",
};

export function categoryLabel(value: string | null | undefined): string {
  if (!value) return DASH;
  return CATEGORY_LABELS[value] || value;
}

/** 审计业务对象类型中文化。 */
export const BUSINESS_TYPE_LABELS: Record<string, string> = {
  task: "采购任务",
  supplier: "供应商",
  order: "采购订单",
  settlement: "对账单",
  contract: "合同",
  invoice: "发票",
  review: "人工审核",
  ai_task: "AI 任务",
};

export function businessTypeLabel(value: string | null | undefined): string {
  if (!value) return DASH;
  return BUSINESS_TYPE_LABELS[value] || value;
}

/** 金额（元）：保留后端精度，最多 6 位小数去尾零。 */
export function formatMoney(value: string | number | null | undefined): string {
  if (value == null || String(value).trim() === "") return DASH;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(parsed);
}

/** 常见英文单位中文化（仅展示层；未知单位原样显示）。 */
export const UNIT_LABELS: Record<string, string> = {
  piece: "个",
  pieces: "个",
  box: "箱",
  boxes: "箱",
  kg: "千克",
  ton: "吨",
  m: "米",
  m2: "平方米",
  set: "套",
  pack: "包",
};

export function unitLabel(unit: string | null | undefined): string {
  if (!unit) return "";
  return UNIT_LABELS[unit.toLowerCase()] || unit;
}

export function formatDateTime(value?: string | null): string {
  if (!value) return DASH;
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export function formatShortDateTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}
