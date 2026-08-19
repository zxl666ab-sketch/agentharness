package com.caijiatai.procurement.interaction;

import jakarta.validation.Valid;
import java.net.URI;
import java.util.List;
import org.springframework.http.ResponseEntity;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api/procurement")
public class HumanInteractionController {
    private final HumanInteractionService service;

    public HumanInteractionController(HumanInteractionService service) {
        this.service = service;
    }

    @GetMapping("/requests/{taskId}/interactions")
    public List<HumanInteractionDtos.View> list(@PathVariable String taskId) {
        return service.list(taskId);
    }

    @GetMapping("/interactions/{interactionId}")
    public HumanInteractionDtos.View detail(@PathVariable String interactionId) {
        return service.detail(interactionId);
    }

    @PostMapping("/interactions/{interactionId}/answer")
    public ResponseEntity<?> answer(
            @PathVariable String interactionId,
            @Valid @RequestBody HumanInteractionDtos.Answer body,
            @RequestHeader("Idempotency-Key") String idempotencyKey) {
        var result = service.answer(interactionId, body, idempotencyKey);
        return ResponseEntity.accepted().location(URI.create(result.location())).body(result);
    }

    @PostMapping(path = "/interactions/{interactionId}/artifacts", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public HumanInteractionDtos.ArtifactView upload(
            @PathVariable String interactionId, @RequestPart("file") MultipartFile file) {
        return service.upload(interactionId, file);
    }

    @PostMapping("/interactions/{interactionId}/retry")
    public ResponseEntity<?> retry(@PathVariable String interactionId) {
        var result = service.retry(interactionId);
        return ResponseEntity.accepted().location(URI.create(result.location())).body(result);
    }

    @PostMapping("/interactions/{interactionId}/cancel")
    public HumanInteractionDtos.View cancel(
            @PathVariable String interactionId,
            @RequestBody(required = false) HumanInteractionDtos.Cancel body) {
        return service.cancel(interactionId, body == null ? new HumanInteractionDtos.Cancel(null) : body);
    }
}
