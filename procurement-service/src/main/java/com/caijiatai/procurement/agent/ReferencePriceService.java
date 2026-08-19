package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.order.PurchaseOrder;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import org.springframework.stereotype.Service;

/**
 * 历史报价 RAG 数据源（K5，冻结设计 4.10）。
 *
 * <p>Java 侧为数据真源：聚合历史已批准订单（订单由已批准任务惰性派生，landed_total 即成交价），
 * 按物料名归一化匹配 + 品类兜底；输出最近 N 条历史成交记录 + 参考区间（p25/p75，BigDecimal）。
 * 历史记录不足 3 条时区间返回 null（样本不足不下结论）。
 * 检索用确定性规则，不引入向量库（Milvus/BGE 为 README 后续扩展）。
 */
@Service
public final class ReferencePriceService {
    static final int MIN_SAMPLE = 3;
    static final int MAX_RECORDS = 20;

    private final ProcurementTaskRepository tasks;
    private final OrderRepository orders;

    public ReferencePriceService(ProcurementTaskRepository tasks, OrderRepository orders) {
        this.tasks = tasks;
        this.orders = orders;
    }

    public Map<String, Object> referencePrices(String taskId, String itemName, String category) {
        var wantedItem = normalize(itemName);
        var wantedCategory = category == null ? "" : category.strip();
        var matches = new ArrayList<Map<String, Object>>();
        for (var order : orders.findAllByLandedTotalNotNullOrderByCreatedAtDesc()) {
            var task = tasks.findById(order.getTaskId()).orElse(null);
            if (task == null || task.getQuantity() == null || task.getQuantity().signum() <= 0
                    || order.getLandedTotal() == null) {
                continue;
            }
            var sameItem = isSimilarItem(wantedItem, normalize(task.getItemName()));
            var sameCategory = wantedItem.isBlank() && !wantedCategory.isBlank()
                    && wantedCategory.equalsIgnoreCase(task.getCategory());
            if (!sameItem && !sameCategory) {
                continue;
            }
            var record = new LinkedHashMap<String, Object>();
            record.put("task_id", order.getTaskId());
            record.put("reference", task.getReference());
            record.put("supplier_name", order.getSupplierName());
            record.put("item_name", task.getItemName());
            record.put("category", task.getCategory());
            record.put("quantity", task.getQuantity().toPlainString());
            record.put("landed_total", order.getLandedTotal().setScale(2, RoundingMode.HALF_UP).toPlainString());
            record.put("landed_unit", order.getLandedTotal()
                    .divide(task.getQuantity(), 4, RoundingMode.HALF_UP).toPlainString());
            record.put("created_at", order.getCreatedAt().toString());
            matches.add(record);
            if (matches.size() >= MAX_RECORDS) {
                break;
            }
        }
        var value = new LinkedHashMap<String, Object>();
        value.put("task_id", taskId);
        value.put("item_name", itemName == null ? "" : itemName);
        value.put("category", category == null ? "" : category);
        value.put("records", matches);
        value.put("interval", interval(matches));
        return value;
    }

    /** p25/p75 参考区间：样本 <3 返回 null；金额 BigDecimal 精确到 2 位，单位价 4 位。 */
    private Map<String, Object> interval(List<Map<String, Object>> records) {
        if (records.size() < MIN_SAMPLE) {
            return null;
        }
        var totals = records.stream()
                .map(record -> new BigDecimal(String.valueOf(record.get("landed_total"))))
                .sorted()
                .toList();
        var units = records.stream()
                .map(record -> new BigDecimal(String.valueOf(record.get("landed_unit"))))
                .sorted()
                .toList();
        return Map.of(
                "p25", percentile(totals, 0.25, 2).toPlainString(),
                "p75", percentile(totals, 0.75, 2).toPlainString(),
                "p25_unit", percentile(units, 0.25, 4).toPlainString(),
                "p75_unit", percentile(units, 0.75, 4).toPlainString(),
                "count", totals.size(),
                "basis", "landed_total_base");
    }

    private BigDecimal percentile(List<BigDecimal> sorted, double quantile, int scale) {
        var index = (int) Math.floor((sorted.size() - 1) * quantile);
        return sorted.get(index).setScale(scale, RoundingMode.HALF_UP);
    }

    /**
     * 物料名相似度（确定性规则，冻结设计 4.10）：归一化后相等，或一方包含另一方
     * （如 "PE快递袋" ↔ "快递袋"、规格前缀差异）。双方均需 ≥2 字符避免过度放宽。
     */
    static boolean isSimilarItem(String wanted, String candidate) {
        if (wanted.isBlank() || candidate.isBlank() || wanted.length() < 2 || candidate.length() < 2) {
            return false;
        }
        return wanted.equals(candidate)
                || wanted.contains(candidate)
                || candidate.contains(wanted);
    }

    static String normalize(String value) {
        if (value == null) {
            return "";
        }
        return value.strip().toLowerCase(Locale.ROOT).replaceAll("\\s+", "");
    }
}
