package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.approval.ProcurementDecisionRepository;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;

/**
 * Serves runtime read endpoints from the Java-side runtime_event projection
 * (replaces the Python reverse-proxy reads once Kafka mode is active).
 */
@Service
public final class RuntimeQueryService {
    private final RuntimeEventRepository events;
    private final ProcurementDecisionRepository decisions;

    public RuntimeQueryService(RuntimeEventRepository events, ProcurementDecisionRepository decisions) {
        this.events = events;
        this.decisions = decisions;
    }

    public Map<String, Object> run(String runId) {
        var rows = events.findByRunId(runId, PageRequest.of(0, 100, Sort.by("globalSeq").ascending()));
        var value = new LinkedHashMap<String, Object>();
        value.put("run_id", runId);
        value.put("status", statusOf(rows));
        value.put("provider", "procurement_agent");
        value.put("model", "procurement-fake-v1");
        value.put("started_at", rows.isEmpty() ? null : rows.getFirst().getOccurredAt());
        value.put("finished_at", rows.stream().map(RuntimeEvent::getOccurredAt).reduce((a, b) -> b).orElse(null));
        value.put("event_count", rows.size());
        value.put("source", "runtime_event_projection");
        return value;
    }

    public List<Map<String, Object>> events(String runId) {
        var rows = events.findByRunId(runId, PageRequest.of(0, 1000, Sort.by("globalSeq").ascending()));
        var result = new ArrayList<Map<String, Object>>();
        for (var row : rows) {
            var value = new LinkedHashMap<String, Object>();
            value.put("event_id", String.valueOf(row.getId()));
            value.put("global_seq", row.getGlobalSeq());
            value.put("run_seq", 0);
            value.put("session_id", "");
            value.put("run_id", row.getRunId() == null ? "" : row.getRunId());
            value.put("type", row.getType());
            value.put("timestamp", row.getOccurredAt().toString());
            value.put("payload", row.getPayload());
            result.add(value);
        }
        return result;
    }

    public List<Map<String, Object>> messages(String runId) {
        var rows = events.findByRunId(runId, PageRequest.of(0, 1000, Sort.by("globalSeq").ascending()));
        var result = new ArrayList<Map<String, Object>>();
        for (var row : rows) {
            if (!"tool_result".equals(row.getType())) {
                continue;
            }
            var value = new LinkedHashMap<String, Object>();
            value.put("id", String.valueOf(row.getId()));
            value.put("role", "tool");
            value.put("content", String.valueOf(row.getPayload().getOrDefault("tool", row.getType())));
            value.put("tool_call_id", String.valueOf(row.getId()));
            value.put("created_at", row.getOccurredAt());
            result.add(value);
        }
        return result;
    }

    public List<Map<String, Object>> toolInvocations(String runId) {
        var rows = events.findByRunId(runId, PageRequest.of(0, 1000, Sort.by("globalSeq").ascending()));
        var result = new ArrayList<Map<String, Object>>();
        for (var row : rows) {
            if (!"tool_call_start".equals(row.getType())) {
                continue;
            }
            var value = new LinkedHashMap<String, Object>();
            value.put("id", String.valueOf(row.getId()));
            value.put("run_id", row.getRunId());
            value.put("tool_name", row.getPayload().getOrDefault("tool", ""));
            value.put("status", "completed");
            value.put("created_at", row.getOccurredAt());
            value.put("arguments", Map.of());
            value.put("result", Map.of());
            result.add(value);
        }
        return result;
    }

    public List<Map<String, Object>> approvals(String runId) {
        var result = new ArrayList<Map<String, Object>>();
        for (var decision : decisions.findByRunIdOrderByCreatedAtAsc(runId)) {
            var value = new LinkedHashMap<String, Object>();
            value.put("id", decision.getApprovalId());
            value.put("run_id", decision.getRunId());
            value.put("tool_call_id", decision.getId());
            value.put("tool_name", "procurement_approve_supplier");
            value.put("effect", decision.getDecision());
            value.put("requires_confirmation", false);
            value.put("decision", decision.getDecision());
            value.put("created_at", decision.getCreatedAt());
            value.put("events", List.of());
            result.add(value);
        }
        return result;
    }

    public Map<String, Object> checkpoint(String runId) {
        var value = new LinkedHashMap<String, Object>();
        value.put("run_id", runId);
        value.put("exists", false);
        value.put("source", "runtime_event_projection");
        return value;
    }

    private String statusOf(List<RuntimeEvent> rows) {
        for (int i = rows.size() - 1; i >= 0; i--) {
            var type = rows.get(i).getType();
            if (type.equals("run_completed")) {
                return "completed";
            }
            if (type.equals("run_failed")) {
                return "failed";
            }
            if (type.equals("run_started") || type.equals("run_status")) {
                return "running";
            }
        }
        return "unknown";
    }
}
