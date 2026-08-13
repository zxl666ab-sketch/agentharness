package com.caijiatai.procurement.report;

import com.caijiatai.procurement.cache.InsightsCache;
import com.caijiatai.procurement.supplier.SupplierService;
import java.util.List;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 统计报表接口（K3，冻结设计 4.8；K4 看板缓存 TTL 60s + 主动失效）。 */
@RestController
@RequestMapping("/api/procurement/insights")
public final class InsightsController {
    private final InsightsService insights;
    private final SupplierService suppliers;
    private final InsightsCache cache;

    public InsightsController(InsightsService insights, SupplierService suppliers, InsightsCache cache) {
        this.insights = insights;
        this.suppliers = suppliers;
        this.cache = cache;
    }

    @GetMapping("/overview")
    public Map<String, Object> overview() {
        return cache.getOrLoad("overview", insights::overview);
    }

    @GetMapping("/trend")
    public List<Map<String, Object>> trend(@RequestParam(defaultValue = "6") int months) {
        return cache.getListOrLoad("trend:" + months, () -> insights.trend(months));
    }

    @GetMapping("/supplier-ranking")
    public List<Map<String, Object>> supplierRanking(
            @RequestParam(defaultValue = "10") int limit) {
        return cache.getListOrLoad("supplier-ranking:" + limit, () -> suppliers.ranking(limit));
    }

    @GetMapping("/categories")
    public List<Map<String, Object>> categories() {
        return cache.getListOrLoad("categories", insights::categories);
    }
}
