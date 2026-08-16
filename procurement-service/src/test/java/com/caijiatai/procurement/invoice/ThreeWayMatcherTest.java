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

    private static ThreeWayMatcher.PurchaseSide purchase(String qty, String landed, String rate) {
        return new ThreeWayMatcher.PurchaseSide(
                qty == null ? null : new BigDecimal(qty), null, new BigDecimal(landed),
                rate == null ? null : new BigDecimal(rate));
    }

    private static ThreeWayMatcher.PurchaseSide purchaseWithReceived(
            String poQty, String received, String landed, String rate) {
        return new ThreeWayMatcher.PurchaseSide(
                poQty == null ? null : new BigDecimal(poQty),
                received == null ? null : new BigDecimal(received),
                new BigDecimal(landed),
                rate == null ? null : new BigDecimal(rate));
    }

    @Test
    void matchesWhenAllFieldsWithinTolerance() {
        var purchase = purchase("1000", "5200.00", "0.13");
        var result = ThreeWayMatcher.match(purchase, invoice("1000", "4601.77", "598.23", "5200.00", "0.13"));
        assertTrue(result.matched());
        assertTrue(result.diffs().isEmpty());
    }

    @Test
    void holdsOnQuantityMismatch() {
        var purchase = purchase("1000", "5200.00", "0.13");
        var result = ThreeWayMatcher.match(purchase, invoice("900", "4601.77", "598.23", "5200.00", "0.13"));
        assertFalse(result.matched());
        assertEquals("quantity", result.diffs().get(0).field());
        assertEquals("-100", result.diffs().get(0).diff());
    }

    @Test
    void holdsOnTotalAndRateMismatch() {
        var purchase = purchase("1000", "5200.00", "0.13");
        var result = ThreeWayMatcher.match(purchase, invoice("1000", "4601.77", "598.23", "5300.00", "0.09"));
        assertFalse(result.matched());
        assertTrue(result.diffs().stream().anyMatch(diff -> diff.field().equals("total_amount")));
        assertTrue(result.diffs().stream().anyMatch(diff -> diff.field().equals("tax_rate")));
    }

    @Test
    void skipsRateWhenExpectedMissing() {
        var purchase = purchase("1000", "5200.00", null);
        var result = ThreeWayMatcher.match(purchase, invoice("1000", "4601.77", "598.23", "5200.00", "0.09"));
        assertTrue(result.matched()); // 税率期望缺失 → 不比对税率
    }

    @Test
    void unitPriceDiffersWhenTotalAndQuantityDisagree() {
        var purchase = purchase("1000", "5200.00", "0.13");
        var result = ThreeWayMatcher.match(purchase, invoice("1000", "5100.00", "663.00", "5763.00", "0.13"));
        assertFalse(result.matched());
        assertTrue(result.diffs().stream().anyMatch(diff -> diff.field().equals("unit_price")));
        assertEquals("5.2", result.expectedUnitPrice().stripTrailingZeros().toPlainString());
        assertEquals("5.763", result.actualUnitPrice().stripTrailingZeros().toPlainString());
    }

    @Test
    void toMapCarriesStructuredDiffs() {
        var purchase = purchase("1000", "5200.00", "0.13");
        var result = ThreeWayMatcher.match(purchase, invoice("900", "4601.77", "598.23", "5200.00", "0.13"));
        var value = result.toMap();
        assertFalse(Boolean.TRUE.equals(value.get("matched")));
        assertEquals(2, ((java.util.List<?>) value.get("diffs")).size()); // 数量 + 单价
    }

    // ---------- M3：收货（GRN）数量参与判定 ----------

    @Test
    void holdsOnReceivedQuantityMismatchWhenGrnPresent() {
        // PO 1000 件、实收 980 件；发票按 1000 开 → 数量应比实收量，判差异
        var purchase = purchaseWithReceived("1000", "980", "5200.00", "0.13");
        var result = ThreeWayMatcher.match(purchase, invoice("1000", "4601.77", "598.23", "5200.00", "0.13"));
        assertFalse(result.matched());
        assertTrue(result.diffs().stream().anyMatch(diff -> diff.field().equals("quantity")));
    }

    @Test
    void matchesWhenInvoiceQuantityEqualsReceivedQuantity() {
        // 发票按实收量开（980）→ 数量与单价口径都以实收量为准，全部一致
        var purchase = purchaseWithReceived("1000", "980", "5096.00", "0.13");
        var result = ThreeWayMatcher.match(purchase, invoice("980", "4510.62", "586.38", "5096.00", "0.13"));
        assertTrue(result.matched());
        assertEquals("5.2", result.expectedUnitPrice().stripTrailingZeros().toPlainString());
    }

    @Test
    void fallsBackToPoQuantityWhenNoReceipt() {
        // 无收货记录（received=null）→ 回退 PO 数量口径，行为与旧版一致
        var purchase = purchaseWithReceived("1000", null, "5200.00", "0.13");
        var result = ThreeWayMatcher.match(purchase, invoice("1000", "4601.77", "598.23", "5200.00", "0.13"));
        assertTrue(result.matched());
    }
}
