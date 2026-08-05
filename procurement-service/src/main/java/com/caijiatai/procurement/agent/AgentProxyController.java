package com.caijiatai.procurement.agent;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public final class AgentProxyController {
    private final RuntimeProxyService proxy;

    public AgentProxyController(RuntimeProxyService proxy) {
        this.proxy = proxy;
    }

    @GetMapping("/api/runtime")
    ResponseEntity<byte[]> runtime() { return proxy.get("/api/runtime"); }

    @GetMapping("/api/procurement/config")
    ResponseEntity<byte[]> procurementConfig() { return proxy.get("/internal/v1/config"); }

    @PostMapping("/api/procurement/config")
    ResponseEntity<byte[]> updateProcurementConfig(@RequestBody byte[] body) {
        return proxy.post("/internal/v1/config", body);
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
