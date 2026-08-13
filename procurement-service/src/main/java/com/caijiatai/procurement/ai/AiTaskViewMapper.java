package com.caijiatai.procurement.ai;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Component;

@Component
public final class AiTaskViewMapper {
    public Map<String, Object> summary(AiTask task) {
        var value = new LinkedHashMap<String, Object>();
        value.put("ai_task_id", task.getId());
        value.put("business_id", task.getBusinessId());
        value.put("generation", task.getGeneration());
        value.put("status", task.getStatus().name());
        value.put("task_type", task.getTaskType().name());
        value.put("trace_id", task.getTraceId());
        value.put("current_step", task.getCurrentStep() == null ? null : task.getCurrentStep().name());
        value.put("progress", task.getProgress());
        value.put("retry_count", task.getRetryCount());
        value.put("max_retries", task.getMaxRetries());
        value.put("retryable", task.isRetryable());
        value.put("operation_id", task.getOperationId());
        value.put("result_id", task.getCurrentResultId());
        value.put("stale", task.isStale());
        value.put("stale_reason", task.getStaleReason());
        value.put("error_category", task.getErrorCategory() == null ? null : task.getErrorCategory().name());
        value.put("error_code", task.getErrorCode());
        value.put("error_message", task.getErrorMessage());
        value.put("assignee", task.getAssignee());
        value.put("created_at", task.getCreatedAt());
        value.put("updated_at", task.getUpdatedAt());
        value.put("started_at", task.getStartedAt());
        value.put("finished_at", task.getFinishedAt());
        return value;
    }

    public Map<String, Object> detail(
            AiTask task,
            List<AiTaskRecord> records,
            AiResult result) {
        var value = new LinkedHashMap<>(summary(task));
        value.put("records", records.stream().map(this::record).toList());
        value.put("result", result == null ? null : result(result));
        return value;
    }

    public Map<String, Object> record(AiTaskRecord record) {
        var value = new LinkedHashMap<String, Object>();
        value.put("record_id", record.getId());
        value.put("ai_task_id", record.getAiTaskId());
        value.put("operation_id", record.getOperationId());
        value.put("attempt", record.getAttempt());
        value.put("sequence", record.getSequence());
        value.put("step", record.getStep().name());
        value.put("status", record.getStatus().name());
        value.put("summary", record.getSummary());
        value.put("error_category", record.getErrorCategory() == null ? null : record.getErrorCategory().name());
        value.put("error_code", record.getErrorCode());
        value.put("error_message", record.getErrorMessage());
        value.put("started_at", record.getStartedAt());
        value.put("finished_at", record.getFinishedAt());
        value.put("duration_ms", record.getDurationMs());
        value.put("created_at", record.getCreatedAt());
        return value;
    }

    public Map<String, Object> result(AiResult result) {
        var value = new LinkedHashMap<String, Object>();
        value.put("ai_result_id", result.getId());
        value.put("ai_task_id", result.getAiTaskId());
        value.put("business_id", result.getBusinessId());
        value.put("generation", result.getGeneration());
        value.put("input_sha256", result.getInputSha256());
        value.put("result_sha256", result.getResultSha256());
        value.put("raw_result", result.getRawResult());
        value.put("structured_result", result.getStructuredResult());
        value.put("sources", result.getSources());
        value.put("provider", result.getProvider());
        value.put("model", result.getModel());
        value.put("prompt_version", result.getPromptVersion());
        value.put("parser_version", result.getParserVersion());
        value.put("stale", result.isStale());
        value.put("stale_reason", result.getStaleReason());
        value.put("created_at", result.getCreatedAt());
        return value;
    }
}
