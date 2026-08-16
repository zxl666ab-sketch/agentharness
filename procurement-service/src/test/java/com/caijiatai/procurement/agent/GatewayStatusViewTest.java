package com.caijiatai.procurement.agent;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class GatewayStatusViewTest {

    private static RuntimeEvent event(long seq, String type, Map<String, Object> payload) {
        return RuntimeEvent.create(seq, null, null, type, payload, Instant.parse("2026-08-16T00:00:00Z"));
    }

    @Test
    void prefersSanitizedHeartbeatSnapshots() {
        var heartbeat = event(100, "heartbeat.ping", Map.of(
                "agent", "python-agent",
                "gateway", List.of(Map.of(
                        "provider", "openai",
                        "state", "open",
                        "remaining_open_s", 42.0,
                        "stats", Map.of("failures", 7),
                        "limits", Map.of("qps", 10),
                        "secret_key", "should-not-leak"))));
        var value = GatewayStatusView.from(Optional.of(heartbeat), List.of());
        assertEquals("heartbeat", value.get("source"));
        @SuppressWarnings("unchecked")
        var providers = (List<Map<String, Object>>) value.get("providers");
        assertEquals(1, providers.size());
        assertEquals("open", providers.get(0).get("state"));
        assertFalse(providers.get(0).containsKey("secret_key"));
        assertFalse(providers.get(0).containsKey("agent"));
    }

    @Test
    void fallsBackToGatewayEventsWithDerivedState() {
        var opened = event(50, "provider_gateway.circuit_opened", Map.of("provider", "openai"));
        var degraded = event(51, "provider_gateway.degraded", Map.of("provider", "openai"));
        var value = GatewayStatusView.from(Optional.empty(), List.of(degraded, opened));
        assertEquals("runtime_events", value.get("source"));
        @SuppressWarnings("unchecked")
        var providers = (List<Map<String, Object>>) value.get("providers");
        assertEquals(1, providers.size());
        // 最新的 provider_gateway.degraded 优先；无事件时 state=active
        assertEquals("degraded", providers.get(0).get("state"));
        assertEquals("provider_gateway.degraded", providers.get(0).get("last_event"));
    }

    @Test
    void reportsNoGatewayEventsWhenAbsent() {
        var value = GatewayStatusView.from(Optional.empty(), List.of());
        assertEquals("no_gateway_events", value.get("source"));
        assertTrue(((List<?>) value.get("providers")).isEmpty());
    }

    @Test
    void mapsEventTypesToStates() {
        assertEquals("open", GatewayStatusView.stateFromEventType("provider_gateway.circuit_open"));
        assertEquals("open", GatewayStatusView.stateFromEventType("provider_gateway.circuit_opened"));
        assertEquals("degraded", GatewayStatusView.stateFromEventType("provider_gateway.degraded"));
        assertEquals("closed", GatewayStatusView.stateFromEventType("provider_gateway.circuit_closed"));
        assertEquals("active", GatewayStatusView.stateFromEventType("provider_gateway.rate_limited"));
        assertEquals("active", GatewayStatusView.stateFromEventType("heartbeat.ping"));
    }
}
