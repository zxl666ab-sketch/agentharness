package com.caijiatai.procurement.report;

import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/procurement/requests")
public final class ProcurementReportController {
    private final ProcurementReportService reports;

    public ProcurementReportController(ProcurementReportService reports) {
        this.reports = reports;
    }

    @GetMapping("/{taskId}/report")
    public Map<String, Object> report(@PathVariable String taskId) {
        return reports.report(taskId);
    }
}
