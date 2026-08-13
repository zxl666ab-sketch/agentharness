package com.caijiatai.procurement.ai;

import jakarta.validation.Valid;
import java.net.URI;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/procurement")
public final class AiTaskController {
    private final AiTaskService service;

    public AiTaskController(AiTaskService service) {
        this.service = service;
    }

    @PostMapping("/requests/{businessId}/ai-tasks")
    public ResponseEntity<Map<String, Object>> create(
            @PathVariable String businessId,
            @Valid @RequestBody(required = false) AiTaskDtos.CreateAiTaskRequest body,
            @RequestHeader(name = "Idempotency-Key", required = false) String headerKey) {
        var type = body == null ? AiTaskType.QUOTE_ANALYSIS : body.taskType();
        var key = headerKey != null && !headerKey.isBlank()
                ? headerKey
                : body == null ? null : body.idempotencyKey();
        var launch = service.create(businessId, type, key);
        return ResponseEntity.accepted()
                .location(URI.create("/api/procurement/ai-tasks/" + launch.task().getId()))
                .body(service.summary(launch.task()));
    }

    @GetMapping("/ai-tasks")
    public Map<String, Object> list(
            @RequestParam(required = false) AiTaskStatus status,
            @RequestParam(name = "task_type", required = false) AiTaskType taskType,
            @RequestParam(name = "business_id", required = false) String businessId,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        return service.list(status, taskType, businessId, page, size);
    }

    @GetMapping("/ai-tasks/{id}")
    public Map<String, Object> detail(@PathVariable String id) {
        return service.detail(id);
    }

    @PostMapping("/ai-tasks/{id}/retry")
    public ResponseEntity<Map<String, Object>> retry(
            @PathVariable String id,
            @RequestHeader(name = "Idempotency-Key") String idempotencyKey) {
        var task = service.retry(id, idempotencyKey, false);
        return ResponseEntity.accepted()
                .location(URI.create("/api/procurement/ai-tasks/" + id))
                .body(service.summary(task));
    }

    @PostMapping("/ai-tasks/{id}/cancel")
    public Map<String, Object> cancel(
            @PathVariable String id,
            @RequestHeader(name = "Idempotency-Key") String idempotencyKey) {
        return service.summary(service.cancel(id, idempotencyKey));
    }
}
