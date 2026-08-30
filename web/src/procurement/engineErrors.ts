// 把引擎/控制面抛出的底层英文错误翻译成采购员能照做的中文提示。
// 仅做展示层映射，不改变错误本身；未命中的消息原样透出。
const PATTERNS: Array<[RegExp, string]> = [
  [/connection error/i,
    "连接模型服务失败：请检查网络或模型服务地址（OPENAI_BASE_URL）是否可达后重试；已上传的报价会保留。"],
  [/is not resumable from status completed/i,
    "上一轮决策证据已记录但任务未推进（通常是分析结果刷新导致审批失效）。请刷新页面后重新提交选定；若仍失败，请复制重开该任务。"],
  [/is not resumable from status/i,
    "Agent 当前状态无法继续，请刷新页面后重试；若仍失败，请使用「恢复采购 Agent」。"],
  [/is active and cannot be resumed/i,
    "Agent 正在处理上一条指令，请稍候再提交。"],
  [/stale_approval/i,
    "比价结果已刷新，此前的审批已失效，请重新选择供应商并提交。"],
  [/analysis_in_flight/i,
    "比价分析进行中，请等待分析完成后再提交选定。"],
  [/approval binding does not match/i,
    "审批信息与系统最新状态不一致，请刷新页面后重新提交选定。"],
  [/approval task version is stale/i,
    "任务已被更新，请刷新页面后重新提交选定。"],
];

export function humanizeEngineError(message: string | null | undefined): string | null | undefined {
  if (!message) return message;
  for (const [pattern, replacement] of PATTERNS) {
    if (pattern.test(message)) return replacement;
  }
  return message;
}
