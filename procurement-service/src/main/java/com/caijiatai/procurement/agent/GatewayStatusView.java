package com.caijiatai.procurement.agent;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * P2-1：LLM 网关状态的脱敏视图（Java 侧只读投影）。
 *
 * 数据来源：Python Agent 心跳（heartbeat.ping payload.gateway，含完整脱敏快照），
 * 回退到最新 provider_gateway.* runtime 事件（熔断/限流/降级/恢复）逐 provider 推导状态。
 * 不暴露任何密钥或内部地址。
 */
public final class GatewayStatusView {

    private GatewayStatusView() {}

    /** 由最新心跳 + 网关事件构造平台接口的 gateway 段。 */
    public static Map<String, Object> from(
            Optional<RuntimeEvent> heartbeat, List<RuntimeEvent> gatewayEvents) {
        var value = new LinkedHashMap<String, Object>();
        if (heartbeat.isPresent()) {
            Object raw = heartbeat.get().getPayload().get("gateway");
            if (raw instanceof List<?> snapshots && !snapshots.isEmpty()) {
                value.put("source", "heartbeat");
                value.put("providers", sanitizeSnapshots(snapshots));
                return value;
            }
        }
        var perProvider = new LinkedHashMap<String, Map<String, Object>>();
        for (var row : gatewayEvents) {
            var payload = row.getPayload();
            var provider = String.valueOf(payload.getOrDefault("provider", "unknown"));
            if (perProvider.containsKey(provider)) {
                continue;
            }
            var detail = new LinkedHashMap<String, Object>();
            detail.put("provider", provider);
            detail.put("state", stateFromEventType(row.getType()));
            detail.put("last_event", row.getType());
            detail.put("occurred_at", row.getOccurredAt().toString());
            perProvider.put(provider, detail);
        }
        value.put("source", perProvider.isEmpty() ? "no_gateway_events" : "runtime_events");
        value.put("providers", List.copyOf(perProvider.values()));
        return value;
    }

    public static String stateFromEventType(String type) {
        if (type.contains("circuit_open") || type.contains("circuit_opened")) {
            return "open";
        }
        if (type.contains("degraded")) {
            return "degraded";
        }
        if (type.contains("circuit_closed")) {
            return "closed";
        }
        return "active";
    }

    /** 只保留心跳快照的允许字段（防御未来字段泄漏）。 */
    private static List<Object> sanitizeSnapshots(List<?> snapshots) {
        var out = new ArrayList<Object>(snapshots.size());
        for (Object item : snapshots) {
            if (!(item instanceof Map<?, ?> raw)) {
                continue;
            }
            var clean = new LinkedHashMap<String, Object>();
            for (String key : List.of("provider", "state", "remaining_open_s", "stats", "limits")) {
                Object value = raw.get(key);
                if (value != null) {
                    clean.put(key, value);
                }
            }
            out.add(clean);
        }
        return out;
    }
}
