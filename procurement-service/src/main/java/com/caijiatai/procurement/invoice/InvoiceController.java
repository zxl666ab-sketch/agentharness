package com.caijiatai.procurement.invoice;

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
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

/** 发票中心接口（P3-1）：上传解析、列表/详情、差异挂起处理、核销。 */
@RestController
@RequestMapping("/api/procurement")
public final class InvoiceController {
    private final InvoiceService invoices;
    private final String operator;

    public InvoiceController(InvoiceService invoices, com.caijiatai.procurement.config.AppProperties properties) {
        this.invoices = invoices;
        this.operator = properties.localOperator();
    }

    @PostMapping(path = "/invoices", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<ProcurementDtos.OperationAccepted> upload(
            @RequestParam("order_id") String orderId,
            @RequestPart("file") MultipartFile file,
            @RequestHeader(name = "Idempotency-Key", required = false) String idempotencyKey) {
        return ResponseEntity.status(202).body(invoices.upload(orderId, file, idempotencyKey));
    }

    @GetMapping("/invoices")
    public Map<String, Object> list(
            @RequestParam(required = false) String status,
            @RequestParam(name = "order_id", required = false) String orderId,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        return invoices.list(status, orderId, page, size);
    }

    @GetMapping("/invoices/{id}")
    public Map<String, Object> detail(@PathVariable String id) {
        return invoices.detail(id);
    }

    @PostMapping("/invoices/{id}/actions")
    public Map<String, Object> action(
            @PathVariable String id,
            @RequestParam String action,
            @RequestBody InvoiceDtos.InvoiceAction body) {
        return invoices.action(id, action, body, operator);
    }
}
