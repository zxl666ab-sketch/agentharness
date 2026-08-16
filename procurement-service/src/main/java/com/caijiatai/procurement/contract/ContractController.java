package com.caijiatai.procurement.contract;

import com.caijiatai.procurement.task.ProcurementDtos;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 合同中心接口（P3-2）：生成草拟、列表/详情、审批/变更/执行/关闭。 */
@RestController
@RequestMapping("/api/procurement")
public final class ContractController {
    private final ContractService contracts;

    public ContractController(ContractService contracts) {
        this.contracts = contracts;
    }

    @PostMapping(path = "/contracts", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<ProcurementDtos.OperationAccepted> createDraft(
            @RequestBody Map<String, Object> body,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        var taskId = String.valueOf(body.getOrDefault("task_id", "")).trim();
        if (taskId.length() != 32) {
            throw new com.caijiatai.procurement.api.ApiException(
                    org.springframework.http.HttpStatus.BAD_REQUEST, "task_id_required", "task_id 必须为 32 位");
        }
        return ResponseEntity.status(202).body(contracts.createDraft(taskId, idempotencyKey));
    }

    @GetMapping("/contracts")
    public Map<String, Object> list(
            @RequestParam(required = false) String status,
            @RequestParam(name = "task_id", required = false) String taskId,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        return contracts.list(status, taskId, page, size);
    }

    @GetMapping("/contracts/{id}")
    public Map<String, Object> detail(@PathVariable String id) {
        return contracts.detail(id);
    }

    @PostMapping("/contracts/{id}/actions")
    public Map<String, Object> action(
            @PathVariable String id,
            @RequestParam String action,
            @RequestBody ContractDtos.ContractAction body) {
        return contracts.action(id, action, body);
    }

    /** 重新草拟（DRAFT 失败重试 / CHANGE_REQUEST 变更修订后重起草）。 */
    @PostMapping(path = "/contracts/{id}/regen-draft")
    public ResponseEntity<ProcurementDtos.OperationAccepted> regenDraft(@PathVariable String id) {
        return ResponseEntity.status(202).body(contracts.regenDraft(id));
    }
}
