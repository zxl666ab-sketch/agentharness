package com.caijiatai.procurement.api;

import com.caijiatai.procurement.agent.AgentClient;
import com.caijiatai.procurement.config.AppProperties;
import java.io.IOException;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import org.springframework.core.io.ClassPathResource;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import tools.jackson.databind.ObjectMapper;

@RestController
public final class HealthController {
    public static final int API_SCHEMA_VERSION = 11;
    public static final String VERSION = "0.5.0";
    private static final List<String> CAPABILITIES = List.of(
            "run_execution_v1",
            "interactive_approval_v1",
            "run_resume_v1",
            "sse_events_v1",
            "tool_execution_v2",
            "verification_reports_v1",
            "procurement_sourcing_v1",
            "procurement_sourcing_v2",
            "procurement_java_control_plane_v1",
            "durable_agent_outbox_v1");

    private final JdbcTemplate jdbc;
    private final RestClient agent;
    private final String token;
    private final String webBuildId;
    private final Instant startedAt = Instant.now();

    public HealthController(
            JdbcTemplate jdbc,
            RestClient agentRestClient,
            AppProperties properties,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.agent = agentRestClient;
        this.token = properties.agentInternalToken();
        this.webBuildId = loadWebBuildId(objectMapper);
    }

    @GetMapping("/api/health")
    public ResponseEntity<Map<String, Object>> health() {
        jdbc.queryForObject("select 1", Integer.class);
        boolean agentAvailable = false;
        Object agentHealth = null;
        try {
            agentHealth = agent.get().uri("/api/health").retrieve().body(Map.class);
            agentAvailable = true;
        } catch (RestClientException ignored) {
            // PostgreSQL determines readiness; Agent health is a separate degraded capability.
        }
        return ResponseEntity.ok(Map.ofEntries(
                Map.entry("service", "procurement-service"),
                Map.entry("status", "ok"),
                Map.entry("backend_version", VERSION),
                Map.entry("api_schema_version", API_SCHEMA_VERSION),
                Map.entry("api_capabilities", CAPABILITIES),
                Map.entry("web_build_id", webBuildId),
                Map.entry("server_started_at", startedAt),
                Map.entry("database", "ready"),
                Map.entry("agent_available", agentAvailable),
                Map.entry("agent_status", agentHealth == null ? Map.of("status", "unavailable") : agentHealth)));
    }

    private String loadWebBuildId(ObjectMapper mapper) {
        var resource = new ClassPathResource("static/build-meta.json");
        if (!resource.exists()) {
            return "development";
        }
        try (var input = resource.getInputStream()) {
            return mapper.readTree(input).path("web_build_id").asText("development");
        } catch (IOException error) {
            return "development";
        }
    }
}
