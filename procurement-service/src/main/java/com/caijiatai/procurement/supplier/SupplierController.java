package com.caijiatai.procurement.supplier;

import com.caijiatai.procurement.cache.InsightsCache;
import java.util.Map;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 供应商档案接口（K1，路径见冻结设计 4.11）。 */
@RestController
@RequestMapping("/api/procurement/suppliers")
public final class SupplierController {
    private final SupplierService suppliers;
    private final InsightsCache insightsCache;

    public SupplierController(SupplierService suppliers, InsightsCache insightsCache) {
        this.suppliers = suppliers;
        this.insightsCache = insightsCache;
    }

    @GetMapping
    public Map<String, Object> list(
            @RequestParam(required = false) String q,
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        return suppliers.list(q, status, page, size);
    }

    @PostMapping
    public Map<String, Object> create(@RequestBody SupplierDtos.SaveRequest body) {
        var value = suppliers.create(body);
        insightsCache.evictAll();
        return value;
    }

    @PutMapping("/{id}")
    public Map<String, Object> update(
            @PathVariable String id, @RequestBody SupplierDtos.SaveRequest body) {
        var value = suppliers.update(id, body);
        insightsCache.evictAll();
        return value;
    }

    @DeleteMapping("/{id}")
    public void delete(@PathVariable String id) {
        suppliers.delete(id);
        insightsCache.evictAll();
    }

    @GetMapping("/{id}/profile")
    public SupplierDtos.Profile profile(@PathVariable String id) {
        return suppliers.profile(id);
    }
}
