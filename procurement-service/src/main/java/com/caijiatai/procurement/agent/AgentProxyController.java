package com.caijiatai.procurement.agent;

import jakarta.servlet.http.HttpServletRequest;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.config.AppProperties;
import org.springframework.core.io.ClassPathResource;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public final class AgentProxyController {
    private final tools.jackson.databind.ObjectMapper mapper;
    private final com.caijiatai.procurement.api.EventStreamService eventStream;
    private final RuntimeQueryService runtimeQuery;
    private final AppProperties properties;
    private final byte[] frozenEvaluation;

    public AgentProxyController(tools.jackson.databind.ObjectMapper mapper,
            com.caijiatai.procurement.api.EventStreamService eventStream,
            RuntimeQueryService runtimeQuery,
            AppProperties properties) {
        this.mapper = mapper;
        this.eventStream = eventStream;
        this.runtimeQuery = runtimeQuery;
        this.properties = properties;
        try {
            this.frozenEvaluation = new ClassPathResource("frozen/frozen-evaluation.json")
                    .getInputStream().readAllBytes();
        } catch (IOException error) {
            throw new IllegalStateException("frozen evaluation bundle missing from classpath", error);
        }
    }

    @GetMapping("/api/runtime")
    Map<String, Object> runtime() {
        if ("demo".equals(properties.agentMode())) {
            return Map.of("status", "available", "source", "synthetic_demo");
        }
        return runtimeQuery.runtime();
    }

    @GetMapping("/api/procurement/config")
    ResponseEntity<byte[]> procurementConfig() {
        return json(envConfig());
    }

    @PostMapping("/api/procurement/config")
    ResponseEntity<byte[]> updateProcurementConfig(@RequestBody byte[] body) {
        // 模型配置以 .env / 环境变量为唯一真源，修改后重启 agent/procurement 生效。
        return json(envConfig());
    }

    private Map<String, Object> envConfig() {
        var env = System.getenv();
        var provider = env.getOrDefault("AGENTHARNESS_PROCUREMENT_PROVIDER", "procurement_fake");
        var apiKey = env.getOrDefault("OPENAI_API_KEY", "");
        var config = new LinkedHashMap<String, Object>();
        config.put("provider", provider);
        config.put("planner_mode", env.getOrDefault(
                "AGENTHARNESS_PROCUREMENT_PLANNER_MODE", "model"));
        config.put("model", provider.equals("openai")
                ? env.getOrDefault("OPENAI_MODEL", "gpt-4o-mini") : "procurement-fake-v1");
        config.put("base_url", env.getOrDefault("OPENAI_BASE_URL", null));
        config.put("api_mode", "auto");
        config.put("reasoning_effort", env.getOrDefault(
                "AGENTHARNESS_PROCUREMENT_REASONING_EFFORT", "none"));
        config.put("api_key_configured", provider.equals("openai") && !apiKey.isBlank());
        // J-M6: no key material (not even a 4-char prefix) in GET responses.
        config.put("input_price_per_million_usd", null);
        config.put("output_price_per_million_usd", null);
        config.put("cached_input_price_per_million_usd", null);
        config.put("max_cost_usd", null);
        return config;
    }

    private ResponseEntity<byte[]> json(Object value) {
        return ResponseEntity.ok()
                .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                .body(mapper.writeValueAsBytes(value));
    }

    @GetMapping("/api/procurement/evaluation")
    ResponseEntity<byte[]> procurementEvaluation() {
        return ResponseEntity.ok()
                .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                .body(frozenEvaluation);
    }



    @GetMapping("/api/runs")
    ResponseEntity<byte[]> runs() {
        return json(Map.of("runs", runtimeQuery.runs(), "source", "runtime_event_projection"));
    }

    @GetMapping("/api/runs/{runId}")
    Object run(@PathVariable String runId) {
        var safe = id(runId);
        return runtimeQuery.run(safe);
    }

    @GetMapping("/api/runs/{runId}/report")
    Object report(@PathVariable String runId) {
        var safe = id(runId);
        return runtimeQuery.report(safe);
    }

    @GetMapping("/api/runs/{runId}/messages")
    Object messages(@PathVariable String runId) {
        var safe = id(runId);
        return json(runtimeQuery.messages(safe));
    }

    @GetMapping("/api/runs/{runId}/events")
    Object events(@PathVariable String runId) {
        var safe = id(runId);
        return json(runtimeQuery.events(safe));
    }

    @GetMapping("/api/runs/{runId}/approvals")
    Object approvals(@PathVariable String runId) {
        var safe = id(runId);
        return json(runtimeQuery.approvals(safe));
    }

    @GetMapping("/api/runs/{runId}/tool-invocations")
    Object invocations(@PathVariable String runId) {
        var safe = id(runId);
        return json(runtimeQuery.toolInvocations(safe));
    }

    @GetMapping("/api/runs/{runId}/checkpoint")
    Object checkpoint(@PathVariable String runId) {
        var safe = id(runId);
        return runtimeQuery.checkpoint(safe);
    }

    @GetMapping("/api/stream")
    Object stream(HttpServletRequest request) {
        return eventStream.stream(streamCursor(
                request.getParameter("after"), request.getHeader("Last-Event-ID")));
    }

    static long streamCursor(String after, String lastEventId) {
        var raw = after != null && !after.isBlank() ? after : lastEventId;
        if (raw == null || raw.isBlank()) {
            return 0L;
        }
        try {
            var cursor = Long.parseLong(raw);
            if (cursor < 0) {
                throw new NumberFormatException("negative cursor");
            }
            return cursor;
        } catch (NumberFormatException error) {
            throw new com.caijiatai.procurement.api.ApiException(
                    org.springframework.http.HttpStatus.BAD_REQUEST,
                    "invalid_event_cursor",
                    "事件游标必须是非负整数");
        }
    }

    private String id(String value) {
        if (!value.matches("[0-9a-f]{32}")) {
            throw new com.caijiatai.procurement.api.ApiException(
                    org.springframework.http.HttpStatus.BAD_REQUEST, "invalid_id", "ID 格式无效");
        }
        return value;
    }

}
