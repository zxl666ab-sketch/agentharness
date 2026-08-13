package com.caijiatai.procurement.review;

import jakarta.validation.Valid;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/procurement/reviews")
public final class ReviewController {
    private final ReviewService service;

    public ReviewController(ReviewService service) {
        this.service = service;
    }

    @GetMapping
    public Map<String, Object> list(
            @RequestParam(required = false) ReviewStatus status,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        return service.list(status, page, size);
    }

    @GetMapping("/{reviewId}")
    public Map<String, Object> detail(@PathVariable String reviewId) {
        return service.detail(reviewId);
    }

    @PostMapping("/{reviewId}/actions")
    public Map<String, Object> action(
            @PathVariable String reviewId,
            @Valid @RequestBody ReviewDtos.ActionRequest body,
            @RequestHeader(name = "Idempotency-Key") String idempotencyKey) {
        return service.action(reviewId, body, idempotencyKey);
    }
}
