package com.caijiatai.procurement.invoice;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.math.BigDecimal;
import java.time.LocalDate;
import org.junit.jupiter.api.Test;

class ThreeWayMatcherTest {

    private static Invoice invoice(
            String qty, String excl, String tax, String total, String rate) {
        return Invoice.register(
                "order-1",
                "INV-001",
                "CODE-1",
                LocalDate.of(2026, 8, 1),
                qty == null ? null : new BigDecimal(qty),
                "个",
                null,
                excl == null ? null : new BigDecimal(excl),
                tax == null ? null : new BigDecimal(tax),
                new BigDecimal(total),
                rate == null ? null : new BigDecimal(rate),
                "供应商A",
                "artifact",
                "sha",
                "invoice-v1");
    }

    @Test
    void matchesWhenAllFieldsWithinTolerance() {
        var purchase = new ThreeWayMatcher.PurchaseSide(
                new BigDecimal("1000"), new BigDecimal("5200.00"), new BigDecimal("0.13"));
        var result = ThreeWayMatcher.match(purchase, invoice("1000", "4601.77", "598.23", "5200.00", "0.13"));
        assertTrue(result.matched());
        assertTrue(result.diffs().isEmpty());
    }

    @Test
    void holdsOnQuantityMismatch() {
        var purchase = new ThreeWayMatcher.PurchaseSide(
                new BigDecimal("1000"), new BigDecimal("5200.00"), new BigDecimal("0.13"));
        var result = ThreeWayMatcher.match(purchase, invoice("900", "4601.77", "598.23", "5200.00", "0.13"));
        assertFalse(result.matched());
        assertEquals("quantity", result.diffs().get(0).field());
        assertEquals("-100", result.diffs().get(0).diff());
    }

    @Test
    void holdsOnTotalAndRateMismatch() {
        var purchase = new ThreeWayMatcher.PurchaseSide(
                new BigDecimal("1000"), new BigDecimal("5200.00"), new BigDecimal("0.13"));
        var result = ThreeWayMatcher.match(purchase, invoice("1000", "4601.77", "598.23", "5300.00", "0.09"));
        assertFalse(result.matched());
        assertTrue(result.diffs().stream().anyMatch(diff -> diff.field().equals("total_amount")));
        assertTrue(result.diffs().stream().anyMatch(diff -> diff.field().equals("tax_rate")));
    }

    @Test
    void skipsRateWhenExpectedMissing() {
        var purchase = new ThreeWayMatcher.PurchaseSide(
                new BigDecimal("1000"), new BigDecimal("5200.00"), null);
        var result = ThreeWayMatcher.match(purchase, invoice("1000", "4601.77", "598.23", "5200.00", "0.09"));
        assertTrue(result.matched()); // 税率期望缺失 → 不比对税率
    }

    @Test
    void unitPriceDiffersWhenTotalAndQuantityDisagree() {
        var purchase = new ThreeWayMatcher.PurchaseSide(
                new BigDecimal("1000"), new BigDecimal("5200.00"), new BigDecimal("0.13"));
        var result = ThreeWayMatcher.match(purchase, invoice("1000", "5100.00", "663.00", "5763.00", "0.13"));
        assertFalse(result.matched());
        assertTrue(result.diffs().stream().anyMatch(diff -> diff.field().equals("unit_price")));
        assertEquals("5.2", result.expectedUnitPrice().stripTrailingZeros().toPlainString());
        assertEquals("5.763", result.actualUnitPrice().stripTrailingZeros().toPlainString());
    }

    @Test
    void toMapCarriesStructuredDiffs() {
        var purchase = new ThreeWayMatcher.PurchaseSide(
                new BigDecimal("1000"), new BigDecimal("5200.00"), new BigDecimal("0.13"));
        var result = ThreeWayMatcher.match(purchase, invoice("900", "4601.77", "598.23", "5200.00", "0.13"));
        var value = result.toMap();
        assertFalse(Boolean.TRUE.equals(value.get("matched")));
        assertEquals(2, ((java.util.List<?>) value.get("diffs")).size()); // 数量 + 单价
    }
}
