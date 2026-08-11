package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.config.AppProperties;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public final class AgentProxyController {
    private final RuntimeProxyService proxy;
    private final AppProperties properties;
    private final tools.jackson.databind.ObjectMapper mapper;

    public AgentProxyController(RuntimeProxyService proxy, AppProperties properties,
            tools.jackson.databind.ObjectMapper mapper) {
        this.proxy = proxy;
        this.properties = properties;
        this.mapper = mapper;
    }

    @GetMapping("/api/runtime")
    ResponseEntity<byte[]> runtime() { return proxy.get("/api/runtime"); }

    @GetMapping("/api/procurement/config")
    ResponseEntity<byte[]> procurementConfig() {
        if ("demo".equals(properties.agentMode())) {
            return json(defaultDemoConfig());
        }
        return proxy.get("/internal/v1/config");
    }

    @PostMapping("/api/procurement/config")
    ResponseEntity<byte[]> updateProcurementConfig(@RequestBody byte[] body) {
        if ("demo".equals(properties.agentMode())) {
            return json(defaultDemoConfig());
        }
        return proxy.post("/internal/v1/config", body);
    }

    private Map<String, Object> defaultDemoConfig() {
        var config = new LinkedHashMap<String, Object>();
        config.put("provider", "procurement_fake");
        config.put("model", "procurement-fake-v1");
        config.put("base_url", null);
        config.put("api_mode", "auto");
        config.put("reasoning_effort", "none");
        config.put("api_key_configured", false);
        config.put("api_key_preview", null);
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
    ResponseEntity<byte[]> procurementEvaluation() { return proxy.get("/internal/v1/evaluation"); }

    @GetMapping("/api/sessions")
    ResponseEntity<byte[]> sessions() { return proxy.get("/api/sessions"); }

    @GetMapping("/api/sessions/{sessionId}/transcript")
    ResponseEntity<byte[]> transcript(@PathVariable String sessionId) {
        return proxy.get("/api/sessions/" + id(sessionId) + "/transcript");
    }

    @GetMapping("/api/runs")
    ResponseEntity<byte[]> runs(HttpServletRequest request) {
        return proxy.get(withQuery("/api/runs", request));
    }

    @GetMapping("/api/runs/{runId}")
    ResponseEntity<byte[]> run(@PathVariable String runId) { return proxy.get("/api/runs/" + id(runId)); }

    @GetMapping("/api/runs/{runId}/report")
    ResponseEntity<byte[]> report(@PathVariable String runId) { return proxy.get("/api/runs/" + id(runId) + "/report"); }

    @GetMapping("/api/runs/{runId}/messages")
    ResponseEntity<byte[]> messages(@PathVariable String runId) { return proxy.get("/api/runs/" + id(runId) + "/messages"); }

    @GetMapping("/api/runs/{runId}/events")
    ResponseEntity<byte[]> events(@PathVariable String runId) { return proxy.get("/api/runs/" + id(runId) + "/events"); }

    @GetMapping("/api/runs/{runId}/approvals")
    ResponseEntity<byte[]> approvals(@PathVariable String runId) { return proxy.get("/api/runs/" + id(runId) + "/approvals"); }

    @GetMapping("/api/runs/{runId}/tool-invocations")
    ResponseEntity<byte[]> invocations(@PathVariable String runId) {
        return proxy.get("/api/runs/" + id(runId) + "/tool-invocations");
    }

    @GetMapping("/api/runs/{runId}/checkpoint")
    ResponseEntity<byte[]> checkpoint(@PathVariable String runId) {
        return proxy.get("/api/runs/" + id(runId) + "/checkpoint");
    }

    @GetMapping("/api/tool-invocations/{invocationId}")
    ResponseEntity<byte[]> invocation(@PathVariable String invocationId) {
        return proxy.get("/api/tool-invocations/" + id(invocationId));
    }

    @GetMapping("/api/stream")
    void stream(HttpServletRequest request, HttpServletResponse response) {
        proxy.stream(request.getQueryString(), request.getHeader("Last-Event-ID"), response);
    }

    private String id(String value) {
        if (!value.matches("[0-9a-f]{32}")) {
            throw new com.caijiatai.procurement.api.ApiException(
                    org.springframework.http.HttpStatus.BAD_REQUEST, "invalid_id", "ID 格式无效");
        }
        return value;
    }

    private String withQuery(String path, HttpServletRequest request) {
        return request.getQueryString() == null ? path : path + "?" + request.getQueryString();
    }
}
