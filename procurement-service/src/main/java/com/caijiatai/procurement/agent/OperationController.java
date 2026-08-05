package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.api.ApiException;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/procurement/operations")
public final class OperationController {
    private final AgentCommandRepository commands;

    public OperationController(AgentCommandRepository commands) {
        this.commands = commands;
    }

    @GetMapping("/{operationId}")
    public Map<String, Object> operation(@PathVariable String operationId) {
        var command = commands.findById(operationId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "operation_not_found", "未找到异步操作"));
        var value = new LinkedHashMap<String, Object>();
        value.put("operation_id", command.getOperationId());
        value.put("operation_type", command.getOperationType());
        value.put("aggregate_id", command.getAggregateId());
        value.put("generation", command.getGeneration());
        value.put("expected_task_version", command.getExpectedTaskVersion());
        value.put("payload_sha256", command.getPayloadSha256());
        value.put("status", command.getStatus());
        value.put("attempt_count", command.getAttemptCount());
        value.put("retryable", "pending".equals(command.getStatus()) || "accepted".equals(command.getStatus()));
        value.put("last_error", command.getLastError());
        value.put("result", command.getResult());
        value.put("accepted_at", command.getAcceptedAt());
        value.put("completed_at", command.getCompletedAt());
        return value;
    }
}
