package com.caijiatai.procurement.report;

import com.caijiatai.procurement.supplier.SupplierService;
import java.util.List;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 统计报表接口（K3，冻结设计 4.8）。 */
@RestController
@RequestMapping("/api/procurement/insights")
public final class InsightsController {
    private final InsightsService insights;
    private final SupplierService suppliers;

    public InsightsController(InsightsService insights, SupplierService suppliers) {
        this.insights = insights;
        this.suppliers = suppliers;
    }

    @GetMapping("/overview")
    public Map<String, Object> overview() {
        return insights.overview();
    }

    @GetMapping("/trend")
    public List<Map<String, Object>> trend(@RequestParam(defaultValue = "6") int months) {
        return insights.trend(months);
    }

    @GetMapping("/supplier-ranking")
    public List<Map<String, Object>> supplierRanking(
            @RequestParam(defaultValue = "10") int limit) {
        return suppliers.ranking(limit);
    }

    @GetMapping("/categories")
    public List<Map<String, Object>> categories() {
        return insights.categories();
    }
}
