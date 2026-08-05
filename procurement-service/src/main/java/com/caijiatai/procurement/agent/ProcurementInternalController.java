package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.approval.PendingDecisionRepository;
import com.caijiatai.procurement.approval.ProcurementDecisionRepository;
import com.caijiatai.procurement.artifact.ProcurementAttachmentRepository;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.TaskViewMapper;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/v1/tasks")
public class ProcurementInternalController {
    private final ProcurementTaskRepository tasks;
    private final ProcurementAttachmentRepository attachments;
    private final ProcurementQuoteRepository quotes;
    private final ComparisonSnapshotRepository snapshots;
    private final PendingDecisionRepository pending;
    private final ProcurementDecisionRepository decisions;
    private final TaskViewMapper views;

    public ProcurementInternalController(
            ProcurementTaskRepository tasks,
            ProcurementAttachmentRepository attachments,
            ProcurementQuoteRepository quotes,
            ComparisonSnapshotRepository snapshots,
            PendingDecisionRepository pending,
            ProcurementDecisionRepository decisions,
            TaskViewMapper views) {
        this.tasks = tasks;
        this.attachments = attachments;
        this.quotes = quotes;
        this.snapshots = snapshots;
        this.pending = pending;
        this.decisions = decisions;
        this.views = views;
    }

    @GetMapping("/{taskId}/context")
    @Transactional(readOnly = true)
    public Map<String, Object> context(@PathVariable String taskId) {
        var task = tasks.findById(taskId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "task_not_found", "未找到采购任务"));
        var current = task.getCurrentSnapshotId() == null ? null
                : snapshots.findByIdAndTaskId(task.getCurrentSnapshotId(), taskId).orElse(null);
        var value = new LinkedHashMap<String, Object>(views.detail(
                task,
                attachments.findByTaskIdOrderByCreatedAtAsc(taskId),
                quotes.findByTaskIdOrderByCreatedAtAsc(taskId),
                current,
                decisions.findByTaskId(taskId).orElse(null)));
        value.put("pending_decisions", pending.findByTaskIdAndStatusIn(
                taskId, List.of("pending", "approved", "stale")).stream().map(item -> {
            var binding = new LinkedHashMap<String, Object>();
            binding.put("pending_decision_id", item.getId());
            binding.put("operation_id", item.getOperationId());
            binding.put("run_id", item.getRunId());
            binding.put("tool_name", item.getToolName());
            binding.put("task_version", item.getTaskVersion());
            binding.put("snapshot_id", item.getSnapshotId());
            binding.put("input_sha256", item.getInputSha256());
            binding.put("business_decision", item.getDecision());
            binding.put("quote_id", item.getQuoteId());
            binding.put("note_hash", item.getNoteHash());
            binding.put("status", item.getStatus());
            return binding;
        }).toList());
        return value;
    }
}
