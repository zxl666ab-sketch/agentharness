package com.caijiatai.procurement.api;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.ConstraintViolationException;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.multipart.MaxUploadSizeExceededException;

@RestControllerAdvice
public final class ApiExceptionHandler {
    public static final String REQUEST_ID_ATTRIBUTE = "caijiatai.request-id";

    @ExceptionHandler(ApiException.class)
    ResponseEntity<ErrorResponse> api(ApiException error, HttpServletRequest request) {
        return response(error.status(), error.code(), error.getMessage(), request, List.of());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<ErrorResponse> validation(
            MethodArgumentNotValidException error, HttpServletRequest request) {
        var fields = error.getBindingResult().getFieldErrors().stream()
                .map(item -> new ErrorResponse.FieldError(
                        item.getField(),
                        item.getCode() == null ? "invalid" : item.getCode(),
                        item.getDefaultMessage() == null ? "字段无效" : item.getDefaultMessage()))
                .toList();
        return response(HttpStatus.UNPROCESSABLE_ENTITY, "validation_failed", "请求字段校验失败", request, fields);
    }

    @ExceptionHandler({ConstraintViolationException.class, HttpMessageNotReadableException.class})
    ResponseEntity<ErrorResponse> malformed(Exception error, HttpServletRequest request) {
        return response(HttpStatus.BAD_REQUEST, "invalid_request", "请求内容无效", request, List.of());
    }

    @ExceptionHandler(MissingRequestHeaderException.class)
    ResponseEntity<ErrorResponse> missingHeader(
            MissingRequestHeaderException error, HttpServletRequest request) {
        return response(HttpStatus.BAD_REQUEST, "missing_header", error.getMessage(), request, List.of());
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    ResponseEntity<ErrorResponse> upload(MaxUploadSizeExceededException error, HttpServletRequest request) {
        return response(HttpStatus.PAYLOAD_TOO_LARGE, "upload_too_large", "上传文件超过允许大小", request, List.of());
    }

    @ExceptionHandler(OptimisticLockingFailureException.class)
    ResponseEntity<ErrorResponse> optimistic(
            OptimisticLockingFailureException error, HttpServletRequest request) {
        return response(HttpStatus.CONFLICT, "task_version_conflict", "采购任务已被其他操作修改", request, List.of());
    }

    /** 注册式状态机非法流转统一 409（不依赖调用方先 can() 的隐式约定）。 */
    @ExceptionHandler(com.caijiatai.procurement.platform.statemachine.IllegalStateTransition.class)
    ResponseEntity<ErrorResponse> illegalTransition(
            com.caijiatai.procurement.platform.statemachine.IllegalStateTransition error,
            HttpServletRequest request) {
        return response(HttpStatus.CONFLICT, "invalid_state_transition",
                "业务状态不允许该流转: " + error.getMessage(), request, List.of());
    }

    private ResponseEntity<ErrorResponse> response(
            HttpStatus status,
            String code,
            String message,
            HttpServletRequest request,
            List<ErrorResponse.FieldError> fields) {
        var requestId = requestId(request);
        return ResponseEntity.status(status)
                .header("X-Request-Id", requestId)
                .body(new ErrorResponse(code, message, status.value(), requestId, Instant.now(), fields));
    }

    static String requestId(HttpServletRequest request) {
        var existing = request.getAttribute(REQUEST_ID_ATTRIBUTE);
        if (existing instanceof String value) {
            return value;
        }
        var value = UUID.randomUUID().toString();
        request.setAttribute(REQUEST_ID_ATTRIBUTE, value);
        return value;
    }
}
