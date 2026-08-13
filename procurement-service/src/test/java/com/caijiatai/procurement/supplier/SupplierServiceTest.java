package com.caijiatai.procurement.supplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.approval.ProcurementDecision;
import com.caijiatai.procurement.approval.ProcurementDecisionRepository;
import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.Pageable;

class SupplierServiceTest {
    private final SupplierRepository suppliers = mock(SupplierRepository.class);
    private final ProcurementQuoteRepository quotes = mock(ProcurementQuoteRepository.class);
    private final ProcurementTaskRepository tasks = mock(ProcurementTaskRepository.class);
    private final ProcurementDecisionRepository decisions = mock(ProcurementDecisionRepository.class);
    private final SupplierService service = new SupplierService(suppliers, quotes, tasks, decisions);

    private Supplier supplier(String name, String status) {
        var supplier = Supplier.create(name, "联系人", "13800000000", "a@b.c", "地址", "包装", "备注");
        supplier.changeStatus(status);
        return supplier;
    }

    private ProcurementQuote quote(String id, String supplierName, String taskId) {
        var extracted = new LinkedHashMap<String, Object>();
        extracted.put("fields", Map.of());
        extracted.put("review_fields", List.of());
        return ProcurementQuote.create(
                taskId, "artifact-" + id, supplierName, id + ".xlsx", "xlsx",
                "sha256-" + id, extracted, "ready", "v1", java.math.BigDecimal.ZERO);
    }

    @Test
    void performanceHalvesWinRateBelowMinimumSampleSize() {
        // 1 次报价 1 次中标：中标率得分 = 60 × 0.5 = 30；活跃度 = min(20, 2) = 2；状态 = 20 → 52 一般
        var supplier = supplier("新供应商", Supplier.STATUS_ACTIVE);
        var quote = quote("q1", "新供应商", "t1");
        when(suppliers.findById(supplier.getId())).thenReturn(Optional.of(supplier));
        when(quotes.findBySupplierNameOrderByCreatedAtAsc("新供应商")).thenReturn(List.of(quote));
        when(decisions.findByQuoteIdIn(List.of(quote.getId())))
                .thenReturn(List.of(decision("d1", quote.getId(), "approved")));

        var profile = service.profile(supplier.getId());

        assertThat(profile.quoteCount()).isEqualTo("1");
        assertThat(profile.winCount()).isEqualTo("1");
        var performance = profile.performance();
        assertThat(performance.winRateScore()).isEqualTo("30.00");
        assertThat(performance.activityScore()).isEqualTo("2.00");
        assertThat(performance.statusScore()).isEqualTo("20.00");
        assertThat(performance.score()).isEqualTo("52.00");
        assertThat(performance.level()).isEqualTo("一般");
    }

    @Test
    void performanceGivesFullWinRateWithEnoughSamples() {
        // 9/10 中标：中标率得分 = 54；活跃度 = 20；状态 = 20 → 94 优质供应商
        var supplier = supplier("老供应商", Supplier.STATUS_ACTIVE);
        var quoteList = java.util.stream.IntStream.range(0, 10)
                .mapToObj(index -> quote("q" + index, "老供应商", "t" + index))
                .toList();
        when(suppliers.findById(supplier.getId())).thenReturn(Optional.of(supplier));
        when(quotes.findBySupplierNameOrderByCreatedAtAsc("老供应商")).thenReturn(quoteList);
        var winning = quoteList.subList(0, 9).stream().map(ProcurementQuote::getId).toList();
        when(decisions.findByQuoteIdIn(any())).thenReturn(
                winning.stream().map(id -> decision("d-" + id, id, "approved")).toList());

        var profile = service.profile(supplier.getId());

        var performance = profile.performance();
        assertThat(performance.winRateScore()).isEqualTo("54.00");
        assertThat(performance.activityScore()).isEqualTo("20.00");
        assertThat(performance.score()).isEqualTo("94.00");
        assertThat(performance.level()).isEqualTo("优质供应商");
    }

    @Test
    void blacklistedSupplierScoreIsCappedAtThirty() {
        var supplier = supplier("问题供应商", Supplier.STATUS_BLACKLISTED);
        var quoteList = java.util.stream.IntStream.range(0, 5)
                .mapToObj(index -> quote("q" + index, "问题供应商", "t" + index))
                .toList();
        when(suppliers.findById(supplier.getId())).thenReturn(Optional.of(supplier));
        when(quotes.findBySupplierNameOrderByCreatedAtAsc("问题供应商")).thenReturn(quoteList);
        when(decisions.findByQuoteIdIn(any())).thenReturn(
                quoteList.stream().map(item -> decision("d-" + item.getId(), item.getId(), "approved")).toList());

        var profile = service.profile(supplier.getId());

        assertThat(profile.performance().score()).isEqualTo("30.00");
        assertThat(profile.performance().level()).isEqualTo("黑名单");
        assertThat(profile.cooperationStatus()).isEqualTo("黑名单");
    }

    @Test
    void pausedSupplierKeepsManualCooperationStatusPriority() {
        var supplier = supplier("暂停供应商", Supplier.STATUS_PAUSED);
        var quote = quote("q1", "暂停供应商", "t1");
        when(suppliers.findById(supplier.getId())).thenReturn(Optional.of(supplier));
        when(quotes.findBySupplierNameOrderByCreatedAtAsc("暂停供应商")).thenReturn(List.of(quote));
        when(decisions.findByQuoteIdIn(any())).thenReturn(List.of(decision("d1", "q1", "approved")));

        var profile = service.profile(supplier.getId());

        assertThat(profile.cooperationStatus()).isEqualTo("已暂停");
        assertThat(profile.performance().statusScore()).isEqualTo("10.00");
    }

    @Test
    void deleteIsRejectedWhenQuotesExist() {
        var supplier = supplier("有关联供应商", Supplier.STATUS_ACTIVE);
        when(suppliers.findById("s1")).thenReturn(Optional.of(supplier));
        when(quotes.findBySupplierNameOrderByCreatedAtAsc("有关联供应商"))
                .thenReturn(List.of(quote("q1", "有关联供应商", "t1")));

        assertThatThrownBy(() -> service.delete("s1"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> {
                    var api = (ApiException) error;
                    assertThat(api.code()).isEqualTo("supplier_has_quotes");
                    assertThat(api.status().value()).isEqualTo(409);
                });
        verify(suppliers, never()).delete(any());
    }

    @Test
    void deleteSucceedsWithoutQuotes() {
        var supplier = supplier("无关联供应商", Supplier.STATUS_ACTIVE);
        when(suppliers.findById("s1")).thenReturn(Optional.of(supplier));
        when(quotes.findBySupplierNameOrderByCreatedAtAsc("无关联供应商")).thenReturn(List.of());

        service.delete("s1");

        verify(suppliers).delete(supplier);
    }

    @Test
    void createRejectsDuplicateName() {
        when(suppliers.findByName("重名供应商")).thenReturn(Optional.of(supplier("重名供应商", Supplier.STATUS_ACTIVE)));

        assertThatThrownBy(() -> service.create(new SupplierDtos.SaveRequest(
                "重名供应商", null, null, null, null, null, null, null)))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code()).isEqualTo("supplier_name_conflict"));
    }

    @Test
    void updateRejectsNameChange() {
        var supplier = supplier("原名", Supplier.STATUS_ACTIVE);
        when(suppliers.findById("s1")).thenReturn(Optional.of(supplier));

        assertThatThrownBy(() -> service.update("s1", new SupplierDtos.SaveRequest(
                "新名", null, null, null, null, null, null, null)))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code()).isEqualTo("supplier_name_immutable"));
    }

    @Test
    void profileAggregatesItemsAndRecentQuotesWithTaskReference() {
        var supplier = supplier("档案供应商", Supplier.STATUS_ACTIVE);
        var task1 = ProcurementTask.structured(1, "物料A采购", "ecommerce_packaging", "物料A",
                java.math.BigDecimal.ONE, "piece", Map.of(), Map.of());
        var task2 = ProcurementTask.structured(1, "物料B采购", "ecommerce_packaging", "物料B",
                java.math.BigDecimal.ONE, "piece", Map.of(), Map.of());
        var quote1 = quote("q1", "档案供应商", task1.getId());
        var quote2 = quote("q2", "档案供应商", task2.getId());
        when(suppliers.findById("s1")).thenReturn(Optional.of(supplier));
        when(quotes.findBySupplierNameOrderByCreatedAtAsc("档案供应商")).thenReturn(List.of(quote1, quote2));
        when(decisions.findByQuoteIdIn(any()))
                .thenReturn(List.of(decision("d1", quote1.getId(), "approved")));
        when(tasks.findAllById(any())).thenReturn(List.of(task1, task2));

        var profile = service.profile("s1");

        assertThat(profile.items()).containsExactlyInAnyOrder("物料A", "物料B");
        assertThat(profile.recentQuotes()).hasSize(2);
        assertThat(profile.recentQuotes()).extracting("taskReference").contains(task1.getReference(), task2.getReference());
        assertThat(profile.winCount()).isEqualTo("1");
    }

    @Test
    void listIncludesPerformanceForEverySupplier() {
        var supplier = supplier("列表供应商", Supplier.STATUS_ACTIVE);
        when(suppliers.findAll(any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(supplier)));
        when(quotes.findAllByOrderByCreatedAtDesc()).thenReturn(List.of());

        var result = service.list(null, null, 0, 20);

        assertThat(result.get("total")).isEqualTo(1L);
        @SuppressWarnings("unchecked")
        var items = (List<Map<String, Object>>) result.get("items");
        assertThat(items).hasSize(1);
        assertThat(items.getFirst()).containsKeys(
                "id", "name", "status", "quote_count", "win_count", "win_rate", "performance");
    }

    private ProcurementDecision decision(String id, String quoteId, String decision) {
        var pending = com.caijiatai.procurement.approval.PendingDecision.create(
                "pending-" + id, "op-" + id, "t1", "run1", 1, "snap1", "sha1",
                decision, quoteId, "hash1");
        pending.approve("approval-" + id, "args-" + id, "formal_java_confirmation", Instant.now());
        return ProcurementDecision.create(pending, null, "operator");
    }
}
