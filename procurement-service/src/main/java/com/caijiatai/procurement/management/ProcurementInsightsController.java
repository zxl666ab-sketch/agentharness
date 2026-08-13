package com.caijiatai.procurement.management;

import java.util.List;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 采购管理看板只读接口：供应商档案、采购订单、全局审计日志。 */
@RestController
@RequestMapping("/api/procurement")
public final class ProcurementInsightsController {
    private final ProcurementInsightsService service;

    public ProcurementInsightsController(ProcurementInsightsService service) {
        this.service = service;
    }

    @GetMapping("/suppliers")
    public List<Map<String, Object>> suppliers() {
        return service.suppliers();
    }

    @GetMapping("/orders")
    public List<Map<String, Object>> orders() {
        return service.orders();
    }

    @GetMapping("/audit-events")
    public Map<String, Object> auditEvents(
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "100") int size) {
        return service.auditEvents(page, size);
    }
}
