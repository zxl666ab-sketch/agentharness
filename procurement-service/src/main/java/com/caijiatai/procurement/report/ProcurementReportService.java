package com.caijiatai.procurement.report;

import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.approval.ProcurementDecisionRepository;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.TaskViewMapper;
import java.util.LinkedHashMap;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ProcurementReportService {
    private final ProcurementTaskRepository tasks;
    private final ProcurementQuoteRepository quotes;
    private final ComparisonSnapshotRepository snapshots;
    private final ProcurementDecisionRepository decisions;
    private final BusinessArtifactRepository artifacts;
    private final AuditEventRepository audit;
    private final RuntimeReportProjectionRepository runtimeReports;
    private final TaskViewMapper views;

    public ProcurementReportService(
            ProcurementTaskRepository tasks,
            ProcurementQuoteRepository quotes,
            ComparisonSnapshotRepository snapshots,
            ProcurementDecisionRepository decisions,
            BusinessArtifactRepository artifacts,
            AuditEventRepository audit,
            RuntimeReportProjectionRepository runtimeReports,
            TaskViewMapper views) {
        this.tasks = tasks;
        this.quotes = quotes;
        this.snapshots = snapshots;
        this.decisions = decisions;
        this.artifacts = artifacts;
        this.audit = audit;
        this.runtimeReports = runtimeReports;
        this.views = views;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> report(String taskId) {
        var task = tasks.findById(taskId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "task_not_found", "未找到采购任务"));
        var taskQuotes = quotes.findByTaskIdOrderByCreatedAtAsc(taskId);
        var snapshot = task.getCurrentSnapshotId() == null ? null
                : snapshots.findByIdAndTaskId(task.getCurrentSnapshotId(), taskId).orElse(null);
        var decision = decisions.findByTaskId(taskId).orElse(null);
        var events = audit.findByTaskIdOrderByCreatedAtAscIdAsc(taskId);
        var businessArtifacts = artifacts.findByTaskIdOrderByCreatedAtAsc(taskId);
        var runtime = runtimeReports.findFirstByTaskIdOrderByCreatedAtDesc(taskId).orElse(null);
        var body = new LinkedHashMap<String, Object>();
        body.put("schema_version", 1);
        body.put("request", views.summary(
                task,
                taskQuotes.size(),
                taskQuotes.stream().mapToInt(item -> item.reviewFields().size()).sum(),
                decision));
        body.put("quotes", taskQuotes.stream().map(views::quote).toList());
        body.put("comparison", snapshot == null ? null : views.snapshot(snapshot));
        body.put("decision", decision == null ? null : views.decision(decision));
        body.put("business_artifacts", businessArtifacts.stream().map(item -> Map.of(
                "id", item.getId(),
                "owner", item.getOwnerPrefix(),
                "kind", item.getKind(),
                "sha256", item.getSha256(),
                "filename", item.getFilename(),
                "content_type", item.getContentType(),
                "size_bytes", item.getSizeBytes())).toList());
        body.put("execution_artifacts", businessArtifacts.stream()
                .filter(item -> List.of("purchase_order_draft", "supplier_confirmation_email")
                        .contains(item.getKind()))
                .map(item -> Map.of(
                        "artifact_id", item.getId(),
                        "kind", item.getKind(),
                        "sha256", item.getSha256(),
                        "filename", item.getFilename(),
                        "content_type", item.getContentType(),
                        "summary", "不可变 Java 业务 Artifact"))
                .toList());
        body.put("supplier_history", supplierHistory(taskId, taskQuotes));
        body.put("audit_events", events.stream().map(item -> {
            var event = new LinkedHashMap<String, Object>();
            event.put("id", item.getId());
            event.put("request_id", item.getTaskId());
            event.put("quote_id", item.getQuoteId());
            event.put("run_id", item.getRunId());
            event.put("type", item.getEventType());
            event.put("actor", item.getActor());
            event.put("payload", item.getPayload());
            event.put("created_at", item.getCreatedAt());
            return event;
        }).toList());
        if (runtime == null) {
            body.put("runtime_evidence_status", "unavailable");
            body.put("runtime", Map.of(
                    "run_id", task.getAnalysisRunId() == null ? "" : task.getAnalysisRunId(),
                    "status", "unavailable"));
        } else {
            body.put("runtime_evidence_status", "cached");
            body.put("runtime", Map.of(
                    "run_id", task.getAnalysisRunId(),
                    "status", "cached",
                    "evidence_sha256", runtime.getEvidenceSha256(),
                    "report", runtime.getReport()));
        }
        body.put("evidence_sha256", CanonicalJson.sha256(body));
        return body;
    }

    private Map<String, Object> supplierHistory(
            String taskId, List<com.caijiatai.procurement.quote.ProcurementQuote> taskQuotes) {
        var suppliers = new ArrayList<Map<String, Object>>();
        var seen = new HashSet<String>();
        for (var quote : taskQuotes) {
            if (!seen.add(quote.getSupplierName())) {
                continue;
            }
            var records = new ArrayList<Map<String, Object>>();
            for (var candidate : quotes.findBySupplierNameOrderByCreatedAtAsc(quote.getSupplierName())) {
                var decision = decisions.findByQuoteId(candidate.getId()).orElse(null);
                if (decision == null || !"approved".equals(decision.getDecision())) {
                    continue;
                }
                var sourceTask = tasks.findById(candidate.getTaskId()).orElse(null);
                if (sourceTask != null) {
                    records.add(Map.of(
                            "request_reference", sourceTask.getReference(),
                            "decision_at", decision.getCreatedAt(),
                            "decision", decision.getDecision()));
                }
            }
            var item = new LinkedHashMap<String, Object>();
            item.put("quote_id", quote.getId());
            item.put("supplier_name", quote.getSupplierName());
            item.put("approved_purchase_count", records.size());
            item.put("records", records);
            item.put("evidence", records.isEmpty()
                    ? "暂无本地已批准采购记录"
                    : "基于 MySQL 中的已批准采购决定");
            suppliers.add(item);
        }
        return Map.of("request_id", taskId, "suppliers", suppliers);
    }
}
