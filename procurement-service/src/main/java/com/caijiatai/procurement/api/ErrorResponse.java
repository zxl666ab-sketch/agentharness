package com.caijiatai.procurement.api;

import java.time.Instant;
import java.util.List;

public record ErrorResponse(
        String code,
        String message,
        int status,
        String requestId,
        Instant timestamp,
        List<FieldError> fieldErrors) {

    public record FieldError(String field, String code, String message) {}
}
