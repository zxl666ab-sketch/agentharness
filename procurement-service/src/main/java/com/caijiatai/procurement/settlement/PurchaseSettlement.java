package com.caijiatai.procurement.settlement;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

/**
 * 对账单（K8）：订单流转到 RECEIVED 时自动派生，金额=订单 landed_total；
 * 同一订单只允许一张对账单（order_id UNIQUE）；财务记录禁止级联删除（外键 RESTRICT）。
 */
@Entity
@Table(name = "purchase_settlement")
public class PurchaseSettlement {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "order_id", nullable = false, unique = true, length = 32)
    private String orderId;
    @Column(name = "settlement_no", nullable = false, unique = true, length = 64)
    private String settlementNo;
    @Column(name = "supplier_name", nullable = false, length = 300)
    private String supplierName;
    @Column(name = "total_amount", nullable = false, precision = 60, scale = 18)
    private BigDecimal totalAmount;
    @Column(nullable = false, length = 30)
    private String status;
    @Column(name = "paid_at")
    private Instant paidAt;
    @Column(length = 1000)
    private String notes;
    @Version
    @Column(nullable = false)
    private long version;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected PurchaseSettlement() {}

    public static PurchaseSettlement derive(
            String orderId, String settlementNo, String supplierName, BigDecimal totalAmount) {
        var settlement = new PurchaseSettlement();
        settlement.id = UUID.randomUUID().toString().replace("-", "");
        settlement.orderId = orderId;
        settlement.settlementNo = settlementNo;
        settlement.supplierName = supplierName;
        settlement.totalAmount = totalAmount;
        settlement.status = SettlementStatus.UNSETTLED.wireValue();
        settlement.createdAt = Instant.now();
        settlement.updatedAt = settlement.createdAt;
        return settlement;
    }

    public String getId() { return id; }
    public String getOrderId() { return orderId; }
    public String getSettlementNo() { return settlementNo; }
    public String getSupplierName() { return supplierName; }
    public BigDecimal getTotalAmount() { return totalAmount; }
    public String getStatus() { return status; }
    public Instant getPaidAt() { return paidAt; }
    public String getNotes() { return notes; }
    public long getVersion() { return version; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }

    public void settle(String notes) {
        this.status = SettlementStatus.SETTLED.wireValue();
        if (notes != null) {
            this.notes = notes;
        }
        this.updatedAt = Instant.now();
    }

    public void pay(Instant paidAt, String notes) {
        this.status = SettlementStatus.PAID.wireValue();
        this.paidAt = paidAt;
        if (notes != null) {
            this.notes = notes;
        }
        this.updatedAt = Instant.now();
    }
}
