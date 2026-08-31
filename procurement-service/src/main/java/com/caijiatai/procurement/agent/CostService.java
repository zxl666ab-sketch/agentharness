package com.caijiatai.procurement.agent;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import org.springframework.stereotype.Service;

/**
 * 成本归集：从 runtime_event 的 model_turn_end 投影按模型/任务聚合 token 并计价。
 *
 * <p>口径与 Python runtime 一致：每个 model_turn_end 的 usage 是该轮（非累计）token 数；
 * 成本 = Σ 各模型 tokens × 定价（缓存折扣见 {@link ModelPricingService}）。
 * 未定价模型的 token 如实计入 unpriced_tokens，不折算为 0 成本。
 */
@Service
public final class CostService {
    private static final String TURN_EVENT = "model_turn_end";

    private final RuntimeEventRepository events;
    private final ModelPricingService pricing;

    public CostService(RuntimeEventRepository events, ModelPricingService pricing) {
        this.events = events;
        this.pricing = pricing;
    }

    /** model -> [input, cachedInput, output, turns]（long[4]）。 */
    public Map<String, long[]> tokensByModel(String runId) {
        var result = new LinkedHashMap<String, long[]>();
        for (var row : events.findByRunIdAndType(runId, TURN_EVENT)) {
            accumulate(result, row);
        }
        return result;
    }

    /**
     * 用定价填充 usage 的 estimated_cost_usd / cost_status（键已存在，只改值不新增键）。
     * 全部模型有价 → "estimated"；部分有价 → "partial"（金额为已计价部分之和）；
     * 完全无价 → 保持原值（unknown/null），绝不假装免费。
     */
    public void applyCost(String runId, Map<String, Object> usage) {
        var totals = tokensByModel(runId);
        if (totals.isEmpty()) {
            return;
        }
        BigDecimal sum = BigDecimal.ZERO;
        int pricedModels = 0;
        for (var entry : totals.entrySet()) {
            var cost = pricing.costUsd(entry.getKey(), entry.getValue()[0], entry.getValue()[1],
                    entry.getValue()[2]);
            if (cost.isPresent()) {
                sum = sum.add(cost.get());
                pricedModels++;
            }
        }
        if (pricedModels == 0) {
            return;
        }
        usage.put("estimated_cost_usd", normalize(sum));
        usage.put("cost_status", pricedModels == totals.size() ? "estimated" : "partial");
    }

    /** 平台级汇总：按模型 + 按任务归集，供成本面板一次拉取。 */
    public Map<String, Object> platformSummary() {
        var rows = events.findTop50000ByTypeOrderByGlobalSeqAsc(TURN_EVENT);
        var byModel = new LinkedHashMap<String, long[]>();
        // taskKey -> model -> [input, cached, output, turns]；taskKey = task_id 或 "run:{run_id}"
        var byTask = new TreeMap<String, Map<String, long[]>>();
        for (var row : rows) {
            accumulate(byModel, row);
            var taskKey = row.getTaskId() != null ? row.getTaskId() : "run:" + row.getRunId();
            accumulate(byTask.computeIfAbsent(taskKey, key -> new LinkedHashMap<>()), row);
        }

        var modelRows = new java.util.ArrayList<Map<String, Object>>();
        BigDecimal pricedTotal = BigDecimal.ZERO;
        long unpricedTokens = 0;
        boolean anyPriced = false;
        boolean anyUnpriced = false;
        for (var entry : byModel.entrySet()) {
            var tokens = entry.getValue();
            var cost = pricing.costUsd(entry.getKey(), tokens[0], tokens[1], tokens[2]);
            var value = new LinkedHashMap<String, Object>();
            value.put("model", entry.getKey());
            value.put("input_tokens", tokens[0]);
            value.put("cached_input_tokens", tokens[1]);
            value.put("output_tokens", tokens[2]);
            value.put("model_turns", tokens[3]);
            value.put("cost_usd", cost.map(CostService::normalize).orElse(null));
            value.put("priced", cost.isPresent());
            if (cost.isPresent()) {
                anyPriced = true;
                pricedTotal = pricedTotal.add(cost.get());
            } else {
                anyUnpriced = true;
                unpricedTokens += tokens[0] + tokens[2];
            }
            modelRows.add(value);
        }

        var taskRows = new java.util.ArrayList<Map<String, Object>>();
        for (var task : byTask.entrySet()) {
            BigDecimal taskCost = BigDecimal.ZERO;
            boolean taskPriced = true;
            long turns = 0;
            long tokens = 0;
            for (var entry : task.getValue().entrySet()) {
                var cost = pricing.costUsd(entry.getKey(), entry.getValue()[0], entry.getValue()[1],
                        entry.getValue()[2]);
                if (cost.isPresent()) {
                    taskCost = taskCost.add(cost.get());
                } else {
                    taskPriced = false;
                }
                turns += entry.getValue()[3];
                tokens += entry.getValue()[0] + entry.getValue()[2];
            }
            var value = new LinkedHashMap<String, Object>();
            value.put("task_id", task.getKey().startsWith("run:") ? null : task.getKey());
            value.put("run_id", task.getKey().startsWith("run:") ? task.getKey().substring(4) : null);
            value.put("model_turns", turns);
            value.put("total_tokens", tokens);
            value.put("cost_usd", taskPriced ? normalize(taskCost) : null);
            value.put("priced", taskPriced);
            taskRows.add(value);
        }
        taskRows.sort((left, right) -> {
            var leftCost = (BigDecimal) left.get("cost_usd");
            var rightCost = (BigDecimal) right.get("cost_usd");
            if (leftCost != null && rightCost != null) {
                return rightCost.compareTo(leftCost);
            }
            if (leftCost != null) {
                return -1;
            }
            if (rightCost != null) {
                return 1;
            }
            return Long.compare((Long) right.get("total_tokens"), (Long) left.get("total_tokens"));
        });

        long totalInput = byModel.values().stream().mapToLong(tokens -> tokens[0]).sum();
        long totalCached = byModel.values().stream().mapToLong(tokens -> tokens[1]).sum();
        long totalOutput = byModel.values().stream().mapToLong(tokens -> tokens[2]).sum();
        long totalTurns = byModel.values().stream().mapToLong(tokens -> tokens[3]).sum();

        var totals = new LinkedHashMap<String, Object>();
        totals.put("input_tokens", totalInput);
        totals.put("cached_input_tokens", totalCached);
        totals.put("output_tokens", totalOutput);
        totals.put("model_turns", totalTurns);
        totals.put("cache_hit_rate", totalInput > 0
                ? BigDecimal.valueOf(Math.min(1.0, (double) totalCached / totalInput))
                        .setScale(4, java.math.RoundingMode.HALF_UP)
                : BigDecimal.ZERO);

        var summary = new LinkedHashMap<String, Object>();
        summary.put("cost_status", anyPriced && !anyUnpriced ? "priced"
                : anyPriced ? "partial" : "unpriced");
        summary.put("pricing_configured", pricing.configured());
        summary.put("pricing_snapshot", pricing.snapshot());
        pricing.parseError().ifPresent(error -> summary.put("pricing_error", error));
        summary.put("total_cost_usd", normalize(pricedTotal));
        summary.put("unpriced_tokens", unpricedTokens);
        summary.put("totals", totals);
        summary.put("by_model", modelRows);
        summary.put("by_task", List.copyOf(taskRows));
        return summary;
    }

    private void accumulate(Map<String, long[]> target, RuntimeEvent row) {
        var payload = row.getPayload();
        var usage = payload.get("usage") instanceof Map<?, ?> map ? map : Map.of();
        var model = String.valueOf(payload.getOrDefault("model", "unknown"));
        var tokens = target.computeIfAbsent(model, key -> new long[4]);
        tokens[0] += asLong(usage.get("input_tokens"));
        tokens[1] += asLong(usage.get("cached_input_tokens"));
        tokens[2] += asLong(usage.get("output_tokens"));
        tokens[3] += 1;
    }

    private static long asLong(Object raw) {
        return raw instanceof Number number ? number.longValue() : 0L;
    }

    private static BigDecimal normalize(BigDecimal value) {
        if (value.signum() == 0) {
            return BigDecimal.ZERO;
        }
        return value.stripTrailingZeros();
    }
}
