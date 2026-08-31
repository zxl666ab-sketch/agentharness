package com.caijiatai.procurement.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class CostServiceTest {
    private static final String PRICING_JSON =
            "{\"hy3\":{\"input_per_million_usd\":1,\"output_per_million_usd\":2,"
                    + "\"cached_input_per_million_usd\":0.5}}";
    private static final String RUN = "a".repeat(32);

    private static RuntimeEvent turn(long seq, String taskId, String model, long input, long cached, long output) {
        return RuntimeEvent.create(seq, taskId, RUN, "model_turn_end", Map.of(
                "model", model,
                "usage", Map.of(
                        "input_tokens", input,
                        "cached_input_tokens", cached,
                        "output_tokens", output)),
                Instant.parse("2026-08-31T00:00:00Z"));
    }

    @Test
    void applyCostPricesAllTurnsWhenEveryModelIsPriced() {
        var events = mock(RuntimeEventRepository.class);
        when(events.findByRunIdAndType(RUN, "model_turn_end")).thenReturn(List.of(
                turn(1, "t1", "hy3", 1000, 600, 500),   // 0.0017
                turn(2, "t1", "hy3", 0, 0, 0)));         // 0
        var service = new CostService(events, new ModelPricingService(PRICING_JSON));
        var usage = new LinkedHashMap<String, Object>();
        usage.put("estimated_cost_usd", null);
        usage.put("cost_status", "unknown");

        service.applyCost(RUN, usage);

        assertThat(String.valueOf(usage.get("estimated_cost_usd"))).isEqualTo("0.0017");
        assertThat(usage.get("cost_status")).isEqualTo("estimated");
    }

    @Test
    void applyCostMarksPartialWhenSomeModelUnpricedAndKeepsSumOfPriced() {
        var events = mock(RuntimeEventRepository.class);
        when(events.findByRunIdAndType(RUN, "model_turn_end")).thenReturn(List.of(
                turn(1, "t1", "hy3", 1_000_000, 0, 0),   // 1.0 USD
                turn(2, "t1", "ghost-model", 500, 0, 50)));
        var service = new CostService(events, new ModelPricingService(PRICING_JSON));
        var usage = new LinkedHashMap<String, Object>();

        service.applyCost(RUN, usage);

        assertThat(String.valueOf(usage.get("estimated_cost_usd"))).isEqualTo("1");
        assertThat(usage.get("cost_status")).isEqualTo("partial");
    }

    @Test
    void applyCostLeavesUsageUntouchedWhenNothingIsPriced() {
        var events = mock(RuntimeEventRepository.class);
        when(events.findByRunIdAndType(RUN, "model_turn_end")).thenReturn(List.of(
                turn(1, "t1", "unknown-x", 100, 0, 10)));
        var service = new CostService(events, new ModelPricingService(""));
        var usage = new LinkedHashMap<String, Object>();
        usage.put("estimated_cost_usd", null);
        usage.put("cost_status", "unknown");

        service.applyCost(RUN, usage);

        // 未配置价格：如实保持 unknown/null，绝不解释为 0 成本
        assertThat(usage.get("estimated_cost_usd")).isNull();
        assertThat(usage.get("cost_status")).isEqualTo("unknown");
    }

    @Test
    void platformSummaryAggregatesByModelAndTask() {
        var events = mock(RuntimeEventRepository.class);
        when(events.findTop50000ByTypeOrderByGlobalSeqAsc("model_turn_end")).thenReturn(List.of(
                turn(1, "task-a", "hy3", 1000, 600, 500),      // 0.0017
                turn(2, "task-b", "hy3", 2000, 0, 1000),       // 0.004
                turn(3, null, "mystery", 700, 0, 300)));        // unpriced, run-keyed bucket
        var service = new CostService(events, new ModelPricingService(PRICING_JSON));

        var summary = service.platformSummary();

        assertThat(summary.get("cost_status")).isEqualTo("partial");
        assertThat(summary.get("pricing_configured")).isEqualTo(true);
        assertThat(String.valueOf(summary.get("total_cost_usd"))).isEqualTo("0.0057");
        assertThat(summary.get("unpriced_tokens")).isEqualTo(1000L);
        @SuppressWarnings("unchecked")
        var totals = (Map<String, Object>) summary.get("totals");
        assertThat(totals.get("model_turns")).isEqualTo(3L);
        // cache_hit_rate = 600 / 3700 = 0.16216… → setScale(4,HALF_UP) = 0.1622
        assertThat(String.valueOf(totals.get("cache_hit_rate"))).isEqualTo("0.1622");
        @SuppressWarnings("unchecked")
        var byModel = (List<Map<String, Object>>) summary.get("by_model");
        assertThat(byModel).hasSize(2);
        assertThat(byModel.getFirst().get("priced")).isEqualTo(true);
        @SuppressWarnings("unchecked")
        var byTask = (List<Map<String, Object>>) summary.get("by_task");
        assertThat(byTask).hasSize(3);
        // 已计价任务按成本降序在前，未计价按 token 数排后
        assertThat(byTask.get(0).get("task_id")).isEqualTo("task-b");
        assertThat(byTask.get(1).get("task_id")).isEqualTo("task-a");
        assertThat(byTask.get(2).get("task_id")).isNull();
        assertThat(byTask.get(2).get("run_id")).isEqualTo(RUN);
    }
}
