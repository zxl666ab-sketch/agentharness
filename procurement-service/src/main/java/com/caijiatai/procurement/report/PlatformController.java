package com.caijiatai.procurement.report;

import com.caijiatai.procurement.ai.AiTaskRepository;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.RedisCallback;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 系统信息接口（K6）：版本 / 组件状态 / 解析器 / 规则集 / 模型脱敏状态。
 */
@RestController
@RequestMapping("/api/procurement/platform")
public final class PlatformController {
    private final JdbcTemplate jdbc;
    private final ProcurementQuoteRepository quotes;
    private final ProcurementTaskRepository tasks;
    private final AiTaskRepository aiTasks;
    private final StringRedisTemplate redis;
    private final boolean redisEnabled;

    public PlatformController(
            JdbcTemplate jdbc,
            ProcurementQuoteRepository quotes,
            ProcurementTaskRepository tasks,
            AiTaskRepository aiTasks,
            StringRedisTemplate redis,
            @Value("${app.redis.enabled:false}") boolean redisEnabled) {
        this.jdbc = jdbc;
        this.quotes = quotes;
        this.tasks = tasks;
        this.aiTasks = aiTasks;
        this.redis = redis;
        this.redisEnabled = redisEnabled;
    }

    @GetMapping
    public Map<String, Object> platform() {
        var value = new LinkedHashMap<String, Object>();
        value.put("service", "procurement-service");
        value.put("backend_version", com.caijiatai.procurement.api.HealthController.VERSION);
        value.put("api_schema_version", com.caijiatai.procurement.api.HealthController.API_SCHEMA_VERSION);
        value.put("components", components());
        value.put("parsers", parsers());
        value.put("rulesets", rulesets());
        value.put("model", modelStatus());
        value.put("db", dbStatus());
        value.put("capabilities", List.of(
                "state_machine_engine_v1",
                "supplier_domain_v1",
                "order_lifecycle_v1",
                "settlement_lifecycle_v1",
                "insights_v1",
                "audit_business_scope_v1"));
        return value;
    }

    private Map<String, Object> components() {
        var value = new LinkedHashMap<String, Object>();
        value.put("mysql", "ready");
        value.put("kafka", System.getenv().getOrDefault("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"));
        value.put("agent_mode", System.getenv().getOrDefault("APP_AGENT_MODE", "kafka"));
        value.put("redis", redisStatus());
        return value;
    }

    private String redisStatus() {
        if (!redisEnabled) return "disabled";
        try {
            var pong = redis.execute((RedisCallback<String>) connection -> connection.ping());
            return "PONG".equalsIgnoreCase(pong) ? "ready" : "down (noop-fallback)";
        } catch (RuntimeException error) {
            return "down (noop-fallback)";
        }
    }

    private Map<String, Object> parsers() {
        var versions = quotes.findAllByOrderByCreatedAtDesc().stream()
                .map(quote -> quote.getParserVersion() == null ? "unknown" : quote.getParserVersion())
                .collect(LinkedHashSet::new, LinkedHashSet::add, LinkedHashSet::addAll);
        return Map.of("quote_parser_versions", List.copyOf(versions));
    }

    private Map<String, Object> rulesets() {
        var versions = tasks.findAll().stream()
                .map(task -> task.getSchemaVersion() == 2 ? "dynamic-spec-v2" : "landed-cost-v1")
                .collect(LinkedHashSet::new, LinkedHashSet::add, LinkedHashSet::addAll);
        return Map.of("comparison_rulesets", List.copyOf(versions));
    }

    private Map<String, Object> modelStatus() {
        var env = System.getenv();
        var provider = env.getOrDefault("AGENTHARNESS_PROCUREMENT_PROVIDER", "procurement_fake");
        var apiKey = env.getOrDefault("OPENAI_API_KEY", "");
        var value = new LinkedHashMap<String, Object>();
        value.put("provider", provider);
        value.put("model", provider.equals("openai")
                ? env.getOrDefault("OPENAI_MODEL", "gpt-4o-mini") : "procurement-fake-v1");
        value.put("api_key_configured", provider.equals("openai") && !apiKey.isBlank());
        value.put("api_key_preview", apiKey.isBlank() ? null
                : apiKey.substring(0, Math.min(4, apiKey.length())) + "…");
        value.put("reasoning_effort", env.getOrDefault(
                "AGENTHARNESS_PROCUREMENT_REASONING_EFFORT", "none"));
        return value;
    }

    private Map<String, Object> dbStatus() {
        var value = new LinkedHashMap<String, Object>();
        try {
            jdbc.queryForObject("select 1", Integer.class);
            value.put("status", "ready");
        } catch (RuntimeException error) {
            value.put("status", "down");
            value.put("error", error.getMessage());
        }
        value.put("ai_tasks", aiTasks.count());
        return value;
    }
}
