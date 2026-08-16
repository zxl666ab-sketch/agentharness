package com.caijiatai.procurement.invoice;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 三单匹配引擎（P3-1，纯确定性）：订单（PO）+ 收货单（GRN）+ 发票（Invoice）。
 *
 * 比对字段：数量（0 容差）、单价（±0.01）、总价（±0.01）、税率（±0.1%）；
 * 单价 = 订单到货总价 / 数量 与 发票不含税金额 / 数量 对比；
 * 税率期望值来自批准报价快照 commercial.tax_rate（未配置时跳过该项）。
 * LLM 不参与匹配判断、不计算金额。
 */
public final class ThreeWayMatcher {

    public static final BigDecimal UNIT_PRICE_TOLERANCE = new BigDecimal("0.01");
    public static final BigDecimal TOTAL_TOLERANCE = new BigDecimal("0.01");
    public static final BigDecimal RATE_TOLERANCE = new BigDecimal("0.001");
    public static final BigDecimal QUANTITY_TOLERANCE = BigDecimal.ZERO;

    private ThreeWayMatcher() {}

    /** PO/GRN 侧输入（来自 purchase_order + 比价快照；receivedQuantity 为实收量，可为 null）。 */
    public record PurchaseSide(
            BigDecimal quantity,
            BigDecimal receivedQuantity /* 可为 null：无收货记录时回退 PO 数量 */,
            BigDecimal landedTotal,
            BigDecimal expectedTaxRate /* 可能为 null → 跳过税率比对 */) {

        /** 数量比对口径：有收货记录（GRN）用实收量，否则回退 PO 数量。 */
        public BigDecimal effectiveQuantity() {
            return receivedQuantity != null ? receivedQuantity : quantity;
        }
    }

    public record Diff(String field, String expected, String actual, String diff) {}

    public record MatchResult(
            boolean matched,
            BigDecimal expectedUnitPrice,
            BigDecimal actualUnitPrice,
            List<Diff> diffs) {

        public Map<String, Object> toMap() {
            var value = new LinkedHashMap<String, Object>();
            value.put("matched", matched);
            value.put("expected_unit_price", expectedUnitPrice == null ? null : expectedUnitPrice.toPlainString());
            value.put("actual_unit_price", actualUnitPrice == null ? null : actualUnitPrice.toPlainString());
            value.put("diffs", diffs.stream().map(diff -> Map.of(
                    "field", diff.field(),
                    "expected", diff.expected(),
                    "actual", diff.actual(),
                    "diff", diff.diff())).toList());
            return value;
        }
    }

    public static MatchResult match(PurchaseSide purchase, Invoice invoice) {
        var diffs = new ArrayList<Diff>();
        boolean matched = true;
        var effectiveQuantity = purchase.effectiveQuantity();

        if (effectiveQuantity != null && invoice.getQuantity() != null) {
            if (!within(invoice.getQuantity(), effectiveQuantity, QUANTITY_TOLERANCE)) {
                diffs.add(new Diff("quantity", plain(effectiveQuantity), plain(invoice.getQuantity()),
                        delta(invoice.getQuantity(), effectiveQuantity)));
            }
        }

        BigDecimal expectedUnitPrice = null;
        BigDecimal actualUnitPrice = null;
        if (purchase.landedTotal() != null && effectiveQuantity != null
                && invoice.getTotalAmount() != null && invoice.getQuantity() != null
                && effectiveQuantity.signum() != 0 && invoice.getQuantity().signum() != 0) {
            // 统一含税口径：PO 到货总价/实收数量 vs 发票价税合计/数量
            expectedUnitPrice = purchase.landedTotal().divide(effectiveQuantity, 6, RoundingMode.HALF_UP);
            actualUnitPrice = invoice.getTotalAmount().divide(invoice.getQuantity(), 6, RoundingMode.HALF_UP);
            if (!within(actualUnitPrice, expectedUnitPrice, UNIT_PRICE_TOLERANCE)) {
                diffs.add(new Diff("unit_price", plain(expectedUnitPrice), plain(actualUnitPrice),
                        delta(actualUnitPrice, expectedUnitPrice)));
            }
        }

        if (purchase.landedTotal() != null && invoice.getTotalAmount() != null
                && !within(invoice.getTotalAmount(), purchase.landedTotal(), TOTAL_TOLERANCE)) {
            diffs.add(new Diff("total_amount", plain(purchase.landedTotal()), plain(invoice.getTotalAmount()),
                    delta(invoice.getTotalAmount(), purchase.landedTotal())));
        }

        if (purchase.expectedTaxRate() != null && invoice.getTaxRate() != null
                && !within(invoice.getTaxRate(), purchase.expectedTaxRate(), RATE_TOLERANCE)) {
            diffs.add(new Diff("tax_rate", plain(purchase.expectedTaxRate()), plain(invoice.getTaxRate()),
                    delta(invoice.getTaxRate(), purchase.expectedTaxRate())));
        }

        return new MatchResult(diffs.isEmpty(), expectedUnitPrice, actualUnitPrice, List.copyOf(diffs));
    }

    private static boolean within(BigDecimal actual, BigDecimal expected, BigDecimal tolerance) {
        return actual.subtract(expected).abs().compareTo(tolerance) <= 0;
    }

    private static String plain(BigDecimal value) {
        return value == null ? null : value.stripTrailingZeros().toPlainString();
    }

    private static String delta(BigDecimal left, BigDecimal right) {
        return left.subtract(right).stripTrailingZeros().toPlainString();
    }
}

