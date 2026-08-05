package com.caijiatai.procurement.task;

import jakarta.validation.Valid;
import com.caijiatai.procurement.approval.ApprovalService;
import java.net.URI;
import java.util.List;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api/procurement")
public class ProcurementController {
    private final ProcurementTaskService service;
    private final ApprovalService approvals;

    public ProcurementController(ProcurementTaskService service, ApprovalService approvals) {
        this.service = service;
        this.approvals = approvals;
    }

    @PostMapping(path = "/conversations", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<ProcurementDtos.OperationAccepted> startConversation(
            @RequestPart("message") String message,
            @RequestPart("files") List<MultipartFile> files,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        return accepted(service.startConversation(message, files, idempotencyKey));
    }

    @GetMapping("/requests")
    public List<Map<String, Object>> requests() {
        return service.list();
    }

    @PostMapping("/requests")
    public ResponseEntity<Map<String, Object>> createRequest(
            @Valid @RequestBody ProcurementDtos.Requirement body,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        return ResponseEntity.status(201).body(service.createStructured(body, idempotencyKey));
    }

    @GetMapping("/requests/{taskId}")
    public Map<String, Object> request(@PathVariable String taskId) {
        return service.detail(taskId);
    }

    @DeleteMapping("/requests/{taskId}")
    public Map<String, Object> delete(@PathVariable String taskId) {
        service.delete(taskId);
        return Map.of("request_id", taskId, "deleted", true);
    }

    @PostMapping(path = "/requests/{taskId}/quotes", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<ProcurementDtos.OperationAccepted> uploadQuote(
            @PathVariable String taskId,
            @RequestPart("file") MultipartFile file,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        return accepted(service.uploadQuote(taskId, file, idempotencyKey));
    }

    @PostMapping("/requests/{taskId}/quotes/{quoteId}/corrections")
    public Map<String, Object> correctQuote(
            @PathVariable String taskId,
            @PathVariable String quoteId,
            @Valid @RequestBody ProcurementDtos.QuoteCorrection body) {
        return service.correctQuote(taskId, quoteId, body);
    }

    @PutMapping("/requests/{taskId}/requirement")
    public Map<String, Object> correctRequirement(
            @PathVariable String taskId,
            @Valid @RequestBody ProcurementDtos.Requirement body) {
        return service.correctRequirement(taskId, body);
    }

    @PostMapping("/requests/{taskId}/analyze")
    public ResponseEntity<ProcurementDtos.OperationAccepted> analyze(
            @PathVariable String taskId,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        return accepted(service.analyze(taskId, idempotencyKey));
    }

    @PostMapping("/requests/{taskId}/resume")
    public ResponseEntity<ProcurementDtos.OperationAccepted> resume(
            @PathVariable String taskId,
            @Valid @RequestBody ProcurementDtos.Resume body,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        return accepted(service.resume(taskId, body, idempotencyKey));
    }

    @PostMapping("/requests/{taskId}/reopen")
    public ResponseEntity<Map<String, Object>> reopen(
            @PathVariable String taskId,
            @RequestBody ProcurementDtos.Reopen body,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        return ResponseEntity.status(201).body(service.reopen(taskId, body, idempotencyKey));
    }

    @PostMapping("/requests/{taskId}/decision")
    public ResponseEntity<?> decision(
            @PathVariable String taskId,
            @Valid @RequestBody ProcurementDtos.Decision body,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        var result = approvals.request(taskId, body, idempotencyKey);
        if (result.decision() != null) {
            return ResponseEntity.ok(Map.of(
                    "request_id", taskId,
                    "decision_id", result.decision().getId(),
                    "status", result.decision().getDecision()));
        }
        var command = result.command();
        var value = new ProcurementDtos.OperationAccepted(
                command.getOperationId(), taskId, null, result.pending().getRunId(), "accepted",
                "/api/procurement/operations/" + command.getOperationId());
        return accepted(value);
    }

    private ResponseEntity<ProcurementDtos.OperationAccepted> accepted(
            ProcurementDtos.OperationAccepted value) {
        return ResponseEntity.accepted()
                .location(URI.create(value.location()))
                .body(value);
    }
}
