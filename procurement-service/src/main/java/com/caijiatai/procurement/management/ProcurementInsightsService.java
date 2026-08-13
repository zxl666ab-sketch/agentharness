package com.caijiatai.procurement.management;

import com.caijiatai.procurement.approval.ProcurementDecision;
import com.caijiatai.procurement.approval.ProcurementDecisionRepository;
import com.caijiatai.procurement.artifact.BusinessArtifact;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.comparison.ComparisonSnapshot;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.RoundingMode;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 只读采购管理看板聚合：供应商档案、采购订单、全局审计日志。
 * 全部由已有业务表实时派生，不新增表结构。
 */
@Service
public class ProcurementInsightsService {
    private static final Set<String> ORDER_ARTIFACT_KINDS =
            Set.of("purchase_order_draft", "supplier_confirmation_email");

    private final ProcurementQuoteRepository quotes;
    private final ProcurementTaskRepository tasks;
    private final ProcurementDecisionRepository decisions;
    private final BusinessArtifactRepository artifacts;
    private final ComparisonSnapshotRepository snapshots;
    private final AuditEventRepository audit;

    public ProcurementInsightsService(
            ProcurementQuoteRepository quotes,
            ProcurementTaskRepository tasks,
            ProcurementDecisionRepository decisions,
            BusinessArtifactRepository artifacts,
            ComparisonSnapshotRepository snapshots,
            AuditEventRepository audit) {
        this.quotes = quotes;
        this.tasks = tasks;
        this.decisions = decisions;
        this.artifacts = artifacts;
        this.snapshots = snapshots;
        this.audit = audit;
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> suppliers() {
        var allQuotes = quotes.findAllByOrderByCreatedAtDesc();
        var decisionsByQuote = decisions.findByQuoteIdIn(
                        allQuotes.stream().map(ProcurementQuote::getId).toList())
                .stream()
                .collect(Collectors.toMap(ProcurementDecision::getQuoteId, Function.identity()));
        var taskIds = allQuotes.stream().map(ProcurementQuote::getTaskId)
                .collect(Collectors.toCollection(LinkedHashSet::new));
        var taskById = tasks.findAllById(taskIds).stream()
                .collect(Collectors.toMap(ProcurementTask::getId, Function.identity()));

        var byName = new LinkedHashMap<String, MutableSupplier>();
        for (var quote : allQuotes) {
            var name = quote.getSupplierName() == null || quote.getSupplierName().isBlank()
                    ? "未命名供应商"
                    : quote.getSupplierName();
            var agg = byName.computeIfAbsent(name, MutableSupplier::new);
            agg.quotes.add(quote);
            agg.taskIds.add(quote.getTaskId());
            var decision = decisionsByQuote.get(quote.getId());
            if (decision != null && "approved".equals(decision.getDecision())) {
                agg.winQuoteIds.add(quote.getId());
            }
        }

        var result = new ArrayList<Map<String, Object>>();
        for (var agg : byName.values()) {
            var first = agg.quotes.get(agg.quotes.size() - 1);
            var last = agg.quotes.get(0);
            var items = agg.taskIds.stream()
                    .map(taskById::get)
                    .filter(Objects::nonNull)
                    .map(ProcurementTask::getItemName)
                    .distinct()
                    .limit(10)
                    .toList();
            var recent = agg.quotes.stream().limit(10)
                    .map(quote -> recentQuote(quote, taskById.get(quote.getTaskId())))
                    .toList();
            var item = new LinkedHashMap<String, Object>();
            item.put("supplier_name", agg.name);
            item.put("quote_count", agg.quotes.size());
            item.put("task_count", agg.taskIds.size());
            item.put("win_count", agg.winQuoteIds.size());
            item.put("win_rate", rate(agg.winQuoteIds.size(), agg.quotes.size()));
            item.put("first_quote_at", first.getCreatedAt());
            item.put("last_quote_at", last.getCreatedAt());
            item.put("items", items);
            item.put("recent_quotes", recent);
            item.put("cooperation_status", agg.winQuoteIds.isEmpty() ? "待合作" : "合作中");
            result.add(item);
        }
        result.sort(Comparator
                .comparingInt((Map<String, Object> entry) -> (int) entry.get("quote_count")).reversed()
                .thenComparing(entry -> String.valueOf(entry.get("supplier_name"))));
        return result;
    }

    private Map<String, Object> recentQuote(ProcurementQuote quote, ProcurementTask task) {
        var value = new LinkedHashMap<String, Object>();
        value.put("quote_id", quote.getId());
        value.put("task_id", quote.getTaskId());
        value.put("reference", task == null ? null : task.getReference());
        value.put("item_name", task == null ? null : task.getItemName());
        value.put("source_filename", quote.getSourceFilename());
        value.put("status", quote.getStatus());
        value.put("created_at", quote.getCreatedAt());
        return value;
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> orders() {
        var approved = tasks.findByStatusOrderByUpdatedAtDesc("approved");
        var taskIds = approved.stream().map(ProcurementTask::getId).toList();
        var taskById = approved.stream()
                .collect(Collectors.toMap(ProcurementTask::getId, Function.identity()));
        var quotesByTask = quotes.findByTaskIdInOrderByCreatedAtAsc(taskIds).stream()
                .collect(Collectors.groupingBy(ProcurementQuote::getTaskId));
        var decisionsByTask = decisions.findByTaskIdIn(taskIds).stream()
                .collect(Collectors.toMap(ProcurementDecision::getTaskId, Function.identity()));
        var artifactsByTask = artifacts.findByTaskIdInOrderByCreatedAtAsc(taskIds).stream()
                .collect(Collectors.groupingBy(BusinessArtifact::getTaskId));
        var snapshotIds = approved.stream()
                .map(ProcurementTask::getCurrentSnapshotId)
                .filter(Objects::nonNull)
                .collect(Collectors.toSet());
        var snapshotById = snapshots.findAllById(snapshotIds).stream()
                .collect(Collectors.toMap(ComparisonSnapshot::getId, Function.identity()));

        var result = new ArrayList<Map<String, Object>>();
        for (var task : approved) {
            var decision = decisionsByTask.get(task.getId());
            var quote = findApprovedQuote(quotesByTask.getOrDefault(task.getId(), List.of()),
                    decision == null ? null : decision.getQuoteId(),
                    task.getApprovedQuoteId());
            var snapshot = task.getCurrentSnapshotId() == null ? null
                    : snapshotById.get(task.getCurrentSnapshotId());
            var item = new LinkedHashMap<String, Object>();
            item.put("task_id", task.getId());
            item.put("reference", task.getReference());
            item.put("title", task.getTitle());
            item.put("item_name", task.getItemName());
            item.put("quantity", task.getQuantity());
            item.put("unit", task.getUnit());
            item.put("status", task.getStatus());
            item.put("approved_quote_id", quote == null ? null : quote.getId());
            item.put("supplier_name", quote == null ? null : quote.getSupplierName());
            item.put("decision_id", decision == null ? null : decision.getId());
            item.put("decision_at", decision == null ? null : decision.getCreatedAt());
            item.put("actor", decision == null ? null : decision.getActor());
            item.put("note", decision == null ? null : decision.getNote());
            item.putAll(approvedCost(snapshot, quote == null ? null : quote.getId()));
            item.put("artifacts", artifactsByTask.getOrDefault(task.getId(), List.of()).stream()
                    .filter(artifact -> ORDER_ARTIFACT_KINDS.contains(artifact.getKind()))
                    .map(this::artifactView)
                    .toList());
            result.add(item);
        }
        result.sort(Comparator.comparing(
                (Map<String, Object> entry) -> (Instant) entry.get("decision_at"),
                Comparator.nullsLast(Comparator.reverseOrder())));
        return result;
    }

    private ProcurementQuote findApprovedQuote(
            List<ProcurementQuote> taskQuotes, String decisionQuoteId, String approvedQuoteId) {
        var target = decisionQuoteId != null ? decisionQuoteId : approvedQuoteId;
        if (target == null) {
            return null;
        }
        return taskQuotes.stream()
                .filter(quote -> target.equals(quote.getId()))
                .findFirst()
                .orElse(null);
    }

    private Map<String, Object> approvedCost(ComparisonSnapshot snapshot, String quoteId) {
        var empty = Map.<String, Object>of("landed_total_base", null, "landed_unit_base", null);
        if (snapshot == null || quoteId == null) {
            return empty;
        }
        var result = snapshot.getResult();
        if (!(result.get("quotes") instanceof List<?> rawQuotes)) {
            return empty;
        }
        for (var raw : rawQuotes) {
            if (!(raw instanceof Map<?, ?> quote)) {
                continue;
            }
            if (!quoteId.equals(quote.get("quote_id")) || !(quote.get("cost") instanceof Map<?, ?> cost)) {
                continue;
            }
            return Map.of(
                    "landed_total_base", cost.get("landed_total_base"),
                    "landed_unit_base", cost.get("landed_unit_base"));
        }
        return empty;
    }

    private Map<String, Object> artifactView(BusinessArtifact artifact) {
        var value = new LinkedHashMap<String, Object>();
        value.put("artifact_id", artifact.getId());
        value.put("kind", artifact.getKind());
        value.put("filename", artifact.getFilename());
        value.put("content_type", artifact.getContentType());
        value.put("size_bytes", artifact.getSizeBytes());
        value.put("sha256", artifact.getSha256());
        value.put("created_at", artifact.getCreatedAt());
        return value;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> auditEvents(int page, int size) {
        var pageable = PageRequest.of(Math.max(0, page), Math.min(200, Math.max(1, size)));
        var events = audit.findAllByOrderByCreatedAtDescIdDesc(pageable);
        var taskIds = events.getContent().stream().map(AuditEvent::getTaskId)
                .collect(Collectors.toCollection(LinkedHashSet::new));
        var taskById = tasks.findAllById(taskIds).stream()
                .collect(Collectors.toMap(ProcurementTask::getId, Function.identity()));
        var items = events.getContent().stream()
                .map(event -> eventView(event, taskById.get(event.getTaskId())))
                .toList();
        var value = new LinkedHashMap<String, Object>();
        value.put("items", items);
        value.put("page", events.getNumber());
        value.put("size", events.getSize());
        value.put("total", events.getTotalElements());
        return value;
    }

    private Map<String, Object> eventView(AuditEvent event, ProcurementTask task) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", event.getId());
        value.put("task_id", event.getTaskId());
        value.put("task_reference", task == null ? null : task.getReference());
        value.put("quote_id", event.getQuoteId());
        value.put("run_id", event.getRunId());
        value.put("event_type", event.getEventType());
        value.put("actor", event.getActor());
        value.put("payload", event.getPayload());
        value.put("created_at", event.getCreatedAt());
        return value;
    }

    private double rate(int wins, int total) {
        if (total <= 0) {
            return 0;
        }
        return BigDecimal.valueOf(wins)
                .divide(BigDecimal.valueOf(total), 4, RoundingMode.HALF_UP)
                .doubleValue();
    }

    private static final class MutableSupplier {
        private final String name;
        private final List<ProcurementQuote> quotes = new ArrayList<>();
        private final Set<String> taskIds = new LinkedHashSet<>();
        private final Set<String> winQuoteIds = new LinkedHashSet<>();

        private MutableSupplier(String name) {
            this.name = name;
        }
    }
}
