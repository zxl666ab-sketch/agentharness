package com.caijiatai.procurement.report;

import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.springframework.data.domain.PageRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 全局审计日志接口（K6，兼容原 GET /api/procurement/audit-events，扩展筛选）。 */
@RestController
@RequestMapping("/api/procurement/audit-events")
public final class AuditEventController {
    private final AuditEventRepository audit;
    private final ProcurementTaskRepository tasks;

    public AuditEventController(AuditEventRepository audit, ProcurementTaskRepository tasks) {
        this.audit = audit;
        this.tasks = tasks;
    }

    @GetMapping
    public Map<String, Object> list(
            @RequestParam(required = false) String type,
            @RequestParam(required = false) String actor,
            @RequestParam(required = false) String business_type,
            @RequestParam(required = false) String task_id,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "50") int size) {
        var pageable = PageRequest.of(Math.max(0, page), Math.min(200, Math.max(1, size)));
        var events = audit.search(
                blank(type), blank(actor), blank(business_type), blank(task_id), pageable);
        var taskIds = events.getContent().stream()
                .map(AuditEvent::getTaskId)
                .filter(Objects::nonNull)
                .collect(Collectors.toCollection(LinkedHashSet::new));
        var taskById = tasks.findAllById(taskIds).stream()
                .collect(Collectors.toMap(ProcurementTask::getId, Function.identity()));
        var items = events.getContent().stream()
                .map(event -> view(event, taskById.get(event.getTaskId())))
                .toList();
        var value = new LinkedHashMap<String, Object>();
        value.put("items", items);
        value.put("page", events.getNumber());
        value.put("size", events.getSize());
        value.put("total", events.getTotalElements());
        return value;
    }

    private Map<String, Object> view(AuditEvent event, ProcurementTask task) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", event.getId());
        value.put("task_id", event.getTaskId());
        value.put("task_reference", task == null ? null : task.getReference());
        value.put("quote_id", event.getQuoteId());
        value.put("run_id", event.getRunId());
        value.put("business_type", event.getBusinessType());
        value.put("business_id", event.getBusinessId());
        value.put("event_type", event.getEventType());
        value.put("actor", event.getActor());
        value.put("payload", event.getPayload());
        value.put("created_at", event.getCreatedAt());
        return value;
    }

    private String blank(String value) {
        return value == null ? "" : value.strip();
    }
}
