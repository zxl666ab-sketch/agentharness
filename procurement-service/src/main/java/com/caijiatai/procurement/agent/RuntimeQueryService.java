package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.approval.ProcurementDecisionRepository;
import com.caijiatai.procurement.artifact.BusinessArtifact;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.report.RuntimeReportProjection;
import com.caijiatai.procurement.report.RuntimeReportProjectionRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
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
    private final RuntimeReportProjectionRepository reports;
    private final ProcurementTaskRepository tasks;
    private final BusinessArtifactRepository artifacts;

    public RuntimeQueryService(
            RuntimeEventRepository events,
            ProcurementDecisionRepository decisions,
            RuntimeReportProjectionRepository reports,
            ProcurementTaskRepository tasks,
            BusinessArtifactRepository artifacts) {
        this.events = events;
        this.decisions = decisions;
        this.reports = reports;
        this.tasks = tasks;
        this.artifacts = artifacts;
    }

    public boolean agentAvailable() {
        var heartbeat = events.findFirstByTypeOrderByGlobalSeqDesc("heartbeat.ping");
        return heartbeat != null && Instant.now().minusSeconds(15).isBefore(heartbeat.getOccurredAt());
    }

    public Map<String, Object> runtime() {
        if (!agentAvailable()) {
            throw new com.caijiatai.procurement.api.ApiException(
                    org.springframework.http.HttpStatus.SERVICE_UNAVAILABLE,
                    "agent_unavailable",
                    "Python Agent 暂时不可用");
        }
        return Map.of(
                "status", "available",
                "source", "runtime_event_projection");
    }

    public Map<String, Object> run(String runId) {
        var rows = events.findByRunId(runId, PageRequest.of(0, 100, Sort.by("globalSeq").ascending()));
        var task = tasks.findFirstByAnalysisRunId(runId).orElse(null);
        var value = new LinkedHashMap<String, Object>();
        value.put("run_id", runId);
        value.put("status", statusOf(rows));
        value.put("provider", "procurement_agent");
        value.put("model", "deterministic");
        value.put("session_id", task == null ? null : task.getSessionId());
        value.put("root_run_id", runId);
        value.put("started_at", rows.isEmpty() ? null : rows.getFirst().getOccurredAt());
        value.put("finished_at", rows.stream().map(RuntimeEvent::getOccurredAt).reduce((a, b) -> b).orElse(null));
        value.put("created_at", rows.isEmpty() ? null : rows.getFirst().getOccurredAt());
        value.put("updated_at", rows.isEmpty() ? null : rows.getLast().getOccurredAt());
        value.put("steps", rows.stream().filter(row -> "ai_task.step".equals(row.getType())).count());
        value.put("usage_json", "{}");
        value.put("metadata_json", "{}");
        value.put("event_count", rows.size());
        value.put("source", "runtime_event_projection");
        return value;
    }

    public List<Map<String, Object>> runs() {
        var rows = events.findAll(Sort.by(Sort.Direction.DESC, "globalSeq"));
        var seen = new java.util.LinkedHashSet<String>();
        var result = new ArrayList<Map<String, Object>>();
        for (var row : rows) {
            if (row.getRunId() != null && seen.add(row.getRunId())) {
                result.add(run(row.getRunId()));
            }
        }
        return result;
    }

    public Map<String, Object> report(String runId) {
        var cached = reports.findFirstByRunIdOrderByCreatedAtDesc(runId).orElse(null);
        if (cached != null && cacheIsJavaOwned(cached.getReport())) {
            return cachedReport(cached);
        }

        var rows = events.findByRunId(runId, PageRequest.of(0, 10_000, Sort.by("globalSeq").ascending()));
        var task = tasks.findFirstByAnalysisRunId(runId).orElse(null);
        if (rows.isEmpty() && task == null) {
            throw new com.caijiatai.procurement.api.ApiException(
                    org.springframework.http.HttpStatus.NOT_FOUND,
                    "run_not_found",
                    "未找到运行记录");
        }
        var eventRows = eventRows(rows, task);
        var toolRows = toolInvocations(rows);
        var approvalRows = approvals(runId);
        var artifactRows = businessArtifacts(task);
        var status = statusOf(rows);
        var failures = "failed".equals(status)
                ? List.of("运行事件已标记失败，请查看事件追踪和任务恢复动作。")
                : List.<String>of();

        var verification = new LinkedHashMap<String, Object>();
        verification.put("configured", false);
        verification.put("policy", null);
        verification.put("attempts", List.of());
        verification.put("failure_reasons", failures);

        var source = new LinkedHashMap<String, Object>();
        source.put("run_updated_at", rows.isEmpty() ? null : rows.getLast().getOccurredAt());
        source.put("max_global_seq", rows.stream().mapToLong(RuntimeEvent::getGlobalSeq).max().orElse(0L));
        source.put("event_count", eventRows.size());
        source.put("tool_count", toolRows.size());
        source.put("approval_count", approvalRows.size());
        source.put("artifact_count", artifactRows.size());

        var report = new LinkedHashMap<String, Object>();
        report.put("schema_version", 1);
        report.put("run_id", runId);
        report.put("session_id", task == null ? null : task.getSessionId());
        report.put("as_of", rows.isEmpty() ? null : rows.getLast().getOccurredAt());
        report.put("run", run(runId));
        report.put("conclusion", conclusion(status, failures));
        report.put("verification", verification);
        report.put("workspace_changes", List.of());
        report.put("tools", toolRows);
        report.put("approvals", approvalRows);
        report.put("artifacts", artifactRows);
        report.put("usage", Map.of("total_tokens", 0, "model_turns", 0));
        report.put("events", eventRows);
        report.put("source", source);
        report.put("evidence_sha256", CanonicalJson.sha256(report));
        return report;
    }

    public List<Map<String, Object>> events(String runId) {
        var rows = events.findByRunId(runId, PageRequest.of(0, 1000, Sort.by("globalSeq").ascending()));
        return eventRows(rows, tasks.findFirstByAnalysisRunId(runId).orElse(null));
    }

    public List<Map<String, Object>> messages(String runId) {
        var rows = events.findByRunId(runId, PageRequest.of(0, 1000, Sort.by("globalSeq").ascending()));
        var result = new ArrayList<Map<String, Object>>();
        for (var row : rows) {
            if (!"tool_result".equals(row.getType()) && !"ai_task.step".equals(row.getType())) {
                continue;
            }
            var value = new LinkedHashMap<String, Object>();
            value.put("id", String.valueOf(row.getId()));
            value.put("role", "tool");
            value.put("content", String.valueOf(row.getPayload().getOrDefault(
                    "summary", row.getPayload().getOrDefault("tool", row.getType()))));
            value.put("tool_call_id", String.valueOf(row.getId()));
            value.put("created_at", row.getOccurredAt());
            result.add(value);
        }
        return result;
    }

    public List<Map<String, Object>> toolInvocations(String runId) {
        var rows = events.findByRunId(runId, PageRequest.of(0, 1000, Sort.by("globalSeq").ascending()));
        return toolInvocations(rows);
    }

    private List<Map<String, Object>> toolInvocations(List<RuntimeEvent> rows) {
        var result = new ArrayList<Map<String, Object>>();
        for (var row : rows) {
            if (!"tool_call_start".equals(row.getType())) {
                continue;
            }
            var value = new LinkedHashMap<String, Object>();
            value.put("id", String.valueOf(row.getId()));
            value.put("run_id", row.getRunId());
            value.put("step", result.size());
            value.put("ordinal", result.size());
            value.put("provider_call_id", String.valueOf(row.getId()));
            value.put("tool_name", row.getPayload().getOrDefault("tool", ""));
            value.put("tool_version", "projection-v1");
            value.put("status", "completed");
            value.put("effect", "pure");
            value.put("replay_policy", "idempotent");
            value.put("arguments_sha256", CanonicalJson.sha256(Map.of()));
            value.put("attempt_count", 1);
            value.put("created_at", row.getOccurredAt());
            value.put("arguments", Map.of());
            value.put("result", Map.of("content", row.getPayload()));
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
            value.put("effect", "external_write");
            value.put("requires_confirmation", false);
            value.put("decision", decision.getDecision());
            value.put("status", "resolved");
            value.put("resolved_at", decision.getCreatedAt());
            value.put("invocation_id", decision.getId());
            value.put("arguments_sha256", CanonicalJson.sha256(Map.of(
                    "task_id", decision.getTaskId(),
                    "snapshot_id", decision.getSnapshotId(),
                    "quote_id", Objects.toString(decision.getQuoteId(), ""),
                    "decision", decision.getDecision())));
            value.put("arguments_summary", "Java 控制面正式决定");
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

    private boolean cacheIsJavaOwned(Map<String, Object> report) {
        var raw = report == null ? null : report.get("artifacts");
        if (!(raw instanceof List<?> list)) return true;
        return list.stream().allMatch(item -> item instanceof Map<?, ?> map
                && String.valueOf(map.get("id")).startsWith("jb"));
    }

    private Map<String, Object> cachedReport(RuntimeReportProjection projection) {
        var value = new LinkedHashMap<String, Object>(projection.getReport());
        value.putIfAbsent("schema_version", 1);
        value.putIfAbsent("run_id", projection.getRunId());
        value.putIfAbsent("evidence_sha256", projection.getEvidenceSha256());
        return value;
    }

    private List<Map<String, Object>> eventRows(List<RuntimeEvent> rows, ProcurementTask task) {
        var result = new ArrayList<Map<String, Object>>();
        var sequence = 0;
        for (var row : rows) {
            var value = new LinkedHashMap<String, Object>();
            value.put("event_id", String.valueOf(row.getId()));
            value.put("global_seq", row.getGlobalSeq());
            value.put("run_seq", sequence++);
            value.put("session_id", task == null || task.getSessionId() == null ? "" : task.getSessionId());
            value.put("run_id", row.getRunId() == null ? "" : row.getRunId());
            value.put("type", row.getType());
            value.put("timestamp", row.getOccurredAt().toString());
            value.put("payload", row.getPayload());
            result.add(value);
        }
        return result;
    }

    private List<Map<String, Object>> businessArtifacts(ProcurementTask task) {
        if (task == null) return List.of();
        return artifacts.findByTaskIdOrderByCreatedAtAsc(task.getId()).stream()
                .map(this::artifactValue)
                .toList();
    }

    private Map<String, Object> artifactValue(BusinessArtifact artifact) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", artifact.getId());
        value.put("sha256", artifact.getSha256());
        value.put("content_type", artifact.getContentType());
        value.put("size_bytes", artifact.getSizeBytes());
        value.put("summary", artifact.getFilename());
        value.put("created_at", artifact.getCreatedAt());
        return value;
    }

    private Map<String, Object> conclusion(String status, List<String> failures) {
        var value = new LinkedHashMap<String, Object>();
        if ("completed".equals(status)) {
            value.put("status", "unverified");
            value.put("label", "运行结束");
            value.put("verified", false);
            value.put("reason", "运行事件已完整投影；采购确定性结果由 Java 业务控制面保存。");
        } else if ("failed".equals(status)) {
            value.put("status", "failed");
            value.put("label", "失败");
            value.put("verified", false);
            value.put("reason", failures.isEmpty() ? "运行失败，请查看事件追踪。" : failures.getFirst());
        } else {
            value.put("status", "pending");
            value.put("label", "运行中");
            value.put("verified", false);
            value.put("reason", "运行尚未形成最终结论。");
        }
        return value;
    }
}
