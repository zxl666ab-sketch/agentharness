package com.caijiatai.procurement.supplier;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.approval.ProcurementDecision;
import com.caijiatai.procurement.approval.ProcurementDecisionRepository;
import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 供应商档案服务（K1）。
 *
 * <p>绩效评分口径冻结（docs/platform-upgrade-design.md 4.6）：黑名单强制封顶 30；
 * 中标率得分仅当报价次数 ≥ 3 时全额计入，不足 3 次按 0.5 折减（最小样本量防刷分）；
 * 活跃度 min(20, 报价次数×2)；合作状态 ACTIVE=20 / PAUSED=10 / BLACKLISTED=0。
 * 计算用 BigDecimal 精确到 2 位。
 */
@Service
public class SupplierService {
    private static final BigDecimal SIXTY = new BigDecimal("60");
    private static final BigDecimal HALF = new BigDecimal("0.5");
    private static final BigDecimal TWO = new BigDecimal("2");
    private static final BigDecimal TWENTY = new BigDecimal("20");
    private static final BigDecimal THIRTY = new BigDecimal("30");
    private static final BigDecimal ONE_HUNDRED = new BigDecimal("100");

    private final SupplierRepository suppliers;
    private final ProcurementQuoteRepository quotes;
    private final ProcurementTaskRepository tasks;
    private final ProcurementDecisionRepository decisions;
    private final AuditEventRepository audit;

    public SupplierService(
            SupplierRepository suppliers,
            ProcurementQuoteRepository quotes,
            ProcurementTaskRepository tasks,
            ProcurementDecisionRepository decisions,
            AuditEventRepository audit) {
        this.suppliers = suppliers;
        this.quotes = quotes;
        this.tasks = tasks;
        this.decisions = decisions;
        this.audit = audit;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> list(String q, String status, int page, int size) {
        var pageable = PageRequest.of(Math.max(0, page), Math.min(100, Math.max(1, size)));
        var query = (q == null || q.isBlank()) && (status == null || status.isBlank())
                ? suppliers.findAll(pageable)
                : suppliers.search(q == null ? "" : q.strip(), status == null ? "" : status.strip(), pageable);
        var quotesBySupplier = quoteAggregates();
        var items = query.getContent().stream()
                .map(supplier -> view(supplier,
                        quotesBySupplier.getOrDefault(supplier.getName(), new Aggregation(0, 0))))
                .toList();
        var value = new LinkedHashMap<String, Object>();
        value.put("items", items);
        value.put("page", query.getNumber());
        value.put("size", query.getSize());
        value.put("total", query.getTotalElements());
        return value;
    }

    @Transactional
    public Map<String, Object> create(SupplierDtos.SaveRequest body) {
        if (body.name() == null || body.name().isBlank() || body.name().strip().length() > 300) {
            throw bad("invalid_supplier_name", "供应商名称不能为空且不得超过 300 个字符");
        }
        if (body.status() != null && !isKnownStatus(body.status())) {
            throw bad("invalid_supplier_status", "供应商状态只能是 ACTIVE / PAUSED / BLACKLISTED");
        }
        var name = body.name().strip();
        if (suppliers.findByName(name).isPresent()) {
            throw conflict("supplier_name_conflict", "供应商名称已存在：" + name);
        }
        var supplier = suppliers.save(Supplier.create(
                name,
                body.contactPerson(),
                body.phone(),
                body.email(),
                body.address(),
                body.mainCategories(),
                body.notes()));
        if (body.status() != null && !Supplier.STATUS_ACTIVE.equals(body.status())) {
            supplier.changeStatus(body.status());
            suppliers.save(supplier);
        }
        audit.save(AuditEvent.forBusiness(
                "supplier", supplier.getId(), "supplier_created", "system",
                Map.of("supplier_id", supplier.getId(), "name", supplier.getName(),
                        "status", supplier.getStatus())));
        return view(supplier, quoteAggregate(supplier.getName()));
    }

    @Transactional
    public Map<String, Object> update(String id, SupplierDtos.SaveRequest body) {
        var supplier = suppliers.findById(id)
                .orElseThrow(() -> notFound("supplier_not_found", "未找到供应商档案"));
        if (body.name() != null && !body.name().isBlank()
                && !Objects.equals(supplier.getName(), body.name().strip())) {
            throw bad("supplier_name_immutable", "供应商名称不可修改（报价历史按名称关联）");
        }
        if (body.status() != null) {
            if (!isKnownStatus(body.status())) {
                throw bad("invalid_supplier_status", "供应商状态只能是 ACTIVE / PAUSED / BLACKLISTED");
            }
            if (!Objects.equals(supplier.getStatus(), body.status())) {
                var from = supplier.getStatus();
                supplier.changeStatus(body.status());
                audit.save(AuditEvent.forBusiness(
                        "supplier", supplier.getId(), "supplier_status_changed", "system",
                        Map.of("supplier_id", supplier.getId(), "name", supplier.getName(),
                                "from", from, "to", supplier.getStatus())));
            }
        }
        supplier.updateProfile(
                body.contactPerson(),
                body.phone(),
                body.email(),
                body.address(),
                body.mainCategories(),
                body.notes());
        suppliers.save(supplier);
        audit.save(AuditEvent.forBusiness(
                "supplier", supplier.getId(), "supplier_updated", "system",
                Map.of("supplier_id", supplier.getId(), "name", supplier.getName(),
                        "status", supplier.getStatus())));
        return view(supplier, quoteAggregate(supplier.getName()));
    }

    @Transactional
    public void delete(String id) {
        var supplier = suppliers.findById(id)
                .orElseThrow(() -> notFound("supplier_not_found", "未找到供应商档案"));
        if (!quotes.findBySupplierNameOrderByCreatedAtAsc(supplier.getName()).isEmpty()) {
            throw conflict("supplier_has_quotes",
                    "该供应商存在报价历史，禁止删除；可将状态改为暂停或黑名单");
        }
        audit.save(AuditEvent.forBusiness(
                "supplier", supplier.getId(), "supplier_deleted", "system",
                Map.of("supplier_id", supplier.getId(), "name", supplier.getName())));
        suppliers.delete(supplier);
    }

    @Transactional(readOnly = true)
    public SupplierDtos.Profile profile(String id) {
        var supplier = suppliers.findById(id)
                .orElseThrow(() -> notFound("supplier_not_found", "未找到供应商档案"));
        var quoteList = quotes.findBySupplierNameOrderByCreatedAtAsc(supplier.getName());
        var wins = winCount(quoteList);
        var taskIds = quoteList.stream()
                .map(ProcurementQuote::getTaskId)
                .collect(Collectors.toCollection(LinkedHashSet::new));
        var taskById = tasks.findAllById(taskIds).stream()
                .collect(Collectors.toMap(ProcurementTask::getId, Function.identity()));
        var items = taskById.values().stream()
                .map(ProcurementTask::getItemName)
                .filter(Objects::nonNull)
                .distinct()
                .limit(20)
                .toList();
        var recent = quoteList.stream()
                .sorted(Comparator.comparing(ProcurementQuote::getCreatedAt).reversed())
                .limit(10)
                .map(quote -> new SupplierDtos.ProfileQuote(
                        quote.getId(),
                        quote.getTaskId(),
                        taskReference(taskById.get(quote.getTaskId())),
                        taskById.get(quote.getTaskId()) == null ? null : taskById.get(quote.getTaskId()).getItemName(),
                        quote.getSourceFilename(),
                        quote.getCreatedAt() == null ? null : quote.getCreatedAt().toString()))
                .toList();
        var aggregation = new Aggregation(quoteList.size(), wins.size());
        return new SupplierDtos.Profile(
                supplier.getId(),
                supplier.getName(),
                supplier.getContactPerson(),
                supplier.getPhone(),
                supplier.getEmail(),
                supplier.getAddress(),
                supplier.getMainCategories(),
                supplier.getStatus(),
                supplier.getNotes(),
                cooperationStatus(supplier.getStatus(), aggregation.winCount()),
                String.valueOf(aggregation.quoteCount()),
                String.valueOf(aggregation.winCount()),
                winRate(aggregation).toPlainString(),
                performance(supplier.getStatus(), aggregation),
                items,
                recent,
                supplier.getCreatedAt().toString(),
                supplier.getUpdatedAt().toString());
    }

    // ---------- 视图与聚合 ----------

    /** K3 供应商中标排行：按中标次数/中标率排序，附带绩效分（冻结 4.8）。 */
    @Transactional(readOnly = true)
    public List<Map<String, Object>> ranking(int limit) {
        var result = new ArrayList<Map<String, Object>>();
        for (var supplier : suppliers.findAll()) {
            var aggregation = quoteAggregate(supplier.getName());
            if (aggregation.quoteCount() == 0) {
                continue;
            }
            var value = view(supplier, aggregation);
            result.add(value);
        }
        result.sort(Comparator
                .comparingInt((Map<String, Object> entry) -> (int) entry.get("win_count")).reversed()
                .thenComparing(entry -> new BigDecimal(String.valueOf(entry.get("win_rate"))).negate())
                .thenComparing(entry -> String.valueOf(entry.get("name"))));
        return result.stream().limit(Math.min(50, Math.max(1, limit))).toList();
    }

    private Map<String, Object> view(Supplier supplier, Aggregation aggregation) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", supplier.getId());
        value.put("name", supplier.getName());
        value.put("contact_person", supplier.getContactPerson());
        value.put("phone", supplier.getPhone());
        value.put("email", supplier.getEmail());
        value.put("address", supplier.getAddress());
        value.put("main_categories", supplier.getMainCategories());
        value.put("status", supplier.getStatus());
        value.put("notes", supplier.getNotes());
        value.put("cooperation_status", cooperationStatus(supplier.getStatus(), aggregation.winCount()));
        value.put("quote_count", aggregation.quoteCount());
        value.put("win_count", aggregation.winCount());
        value.put("win_rate", winRate(aggregation));
        value.put("performance", performance(supplier.getStatus(), aggregation));
        value.put("created_at", supplier.getCreatedAt().toString());
        value.put("updated_at", supplier.getUpdatedAt().toString());
        return value;
    }

    private Map<String, SupplierService.Aggregation> quoteAggregates() {
        var byName = new LinkedHashMap<String, List<ProcurementQuote>>();
        quotes.findAllByOrderByCreatedAtDesc().forEach(quote -> {
            var name = quote.getSupplierName() == null || quote.getSupplierName().isBlank()
                    ? "未命名供应商"
                    : quote.getSupplierName();
            byName.computeIfAbsent(name, ignored -> new ArrayList<>()).add(quote);
        });
        var result = new LinkedHashMap<String, Aggregation>();
        byName.forEach((name, list) -> result.put(name, new Aggregation(list.size(), winCount(list).size())));
        return result;
    }

    private Aggregation quoteAggregate(String supplierName) {
        var list = quotes.findBySupplierNameOrderByCreatedAtAsc(supplierName);
        return new Aggregation(list.size(), winCount(list).size());
    }

    private List<ProcurementQuote> winCount(List<ProcurementQuote> quoteList) {
        if (quoteList.isEmpty()) {
            return List.of();
        }
        var decisionIds = quoteList.stream().map(ProcurementQuote::getId).toList();
        var approvedQuoteIds = decisions.findByQuoteIdIn(decisionIds).stream()
                .filter(decision -> "approved".equals(decision.getDecision()))
                .map(ProcurementDecision::getQuoteId)
                .collect(Collectors.toSet());
        return quoteList.stream()
                .filter(quote -> approvedQuoteIds.contains(quote.getId()))
                .toList();
    }

    private String cooperationStatus(String manualStatus, int winCount) {
        return switch (manualStatus) {
            case Supplier.STATUS_PAUSED -> "已暂停";
            case Supplier.STATUS_BLACKLISTED -> "黑名单";
            default -> winCount > 0 ? "合作中" : "待合作";
        };
    }

    private String taskReference(ProcurementTask task) {
        return task == null ? null : task.getReference();
    }

    private boolean isKnownStatus(String status) {
        return Supplier.STATUS_ACTIVE.equals(status)
                || Supplier.STATUS_PAUSED.equals(status)
                || Supplier.STATUS_BLACKLISTED.equals(status);
    }

    private ApiException bad(String code, String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, code, message);
    }

    private ApiException conflict(String code, String message) {
        return new ApiException(HttpStatus.CONFLICT, code, message);
    }

    private ApiException notFound(String code, String message) {
        return new ApiException(HttpStatus.NOT_FOUND, code, message);
    }

    // ---------- 绩效评分（冻结口径 4.6） ----------

    /** 聚合结果：报价次数与中标次数（中标 = 决策 approved 且 quote_id 命中该报价）。 */
    private static final class Aggregation {
        private final int quoteCount;
        private final int winCount;

        private Aggregation(int quoteCount, int winCount) {
            this.quoteCount = quoteCount;
            this.winCount = winCount;
        }

        private int quoteCount() { return quoteCount; }
        private int winCount() { return winCount; }
    }

    private BigDecimal winRate(Aggregation aggregation) {
        if (aggregation.quoteCount() <= 0) {
            return BigDecimal.ZERO.setScale(2, RoundingMode.HALF_UP);
        }
        return BigDecimal.valueOf(aggregation.winCount())
                .divide(BigDecimal.valueOf(aggregation.quoteCount()), 4, RoundingMode.HALF_UP)
                .setScale(2, RoundingMode.HALF_UP);
    }

    private SupplierDtos.Performance performance(String manualStatus, Aggregation aggregation) {
        var quoteCount = aggregation.quoteCount();
        var winCount = aggregation.winCount();
        var winRateScore = winCount <= 0
                ? BigDecimal.ZERO
                : BigDecimal.valueOf(winCount)
                        .divide(BigDecimal.valueOf(quoteCount), 6, RoundingMode.HALF_UP)
                        .multiply(SIXTY);
        if (quoteCount < 3) {
            // 最小样本量：报价次数不足 3 次时中标率不可信，按 0.5 折减（防新供应商虚高）
            winRateScore = winRateScore.multiply(HALF);
        }
        var activityScore = BigDecimal.valueOf(quoteCount)
                .multiply(TWO)
                .min(TWENTY);
        var statusScore = switch (manualStatus) {
            case Supplier.STATUS_ACTIVE -> TWENTY;
            case Supplier.STATUS_PAUSED -> BigDecimal.TEN;
            default -> BigDecimal.ZERO;
        };
        var baseScore = winRateScore.add(activityScore).add(statusScore).min(ONE_HUNDRED);
        var score = Supplier.STATUS_BLACKLISTED.equals(manualStatus)
                ? baseScore.min(THIRTY)   // 黑名单强制封顶 30，即使中标率高
                : baseScore;
        var level = Supplier.STATUS_BLACKLISTED.equals(manualStatus)
                ? "黑名单"
                : levelOf(score);
        return new SupplierDtos.Performance(
                level,
                score.setScale(2, RoundingMode.HALF_UP).toPlainString(),
                winRateScore.setScale(2, RoundingMode.HALF_UP).toPlainString(),
                activityScore.setScale(2, RoundingMode.HALF_UP).toPlainString(),
                statusScore.setScale(2, RoundingMode.HALF_UP).toPlainString(),
                baseScore.setScale(2, RoundingMode.HALF_UP).toPlainString());
    }

    private String levelOf(BigDecimal score) {
        if (score.compareTo(new BigDecimal("80")) >= 0) {
            return "优质供应商";
        }
        if (score.compareTo(new BigDecimal("60")) >= 0) {
            return "良好";
        }
        if (score.compareTo(new BigDecimal("40")) >= 0) {
            return "一般";
        }
        return "待观察";
    }
}
