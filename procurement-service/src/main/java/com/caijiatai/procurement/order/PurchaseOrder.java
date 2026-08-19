package com.caijiatai.procurement.order;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

/**
 * 采购订单（K2）：在正式采购决定事务中生成，UNIQUE(task_id) 防并发双写。
 * 已批准任务的订单不可删除，只可流转或关闭（证据链完整性）。
 */
@Entity
@Table(name = "purchase_order")
public class PurchaseOrder {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "task_id", nullable = false, unique = true, length = 32)
    private String taskId;
    @Column(name = "order_no", nullable = false, unique = true, length = 64)
    private String orderNo;
    @Column(name = "supplier_name", nullable = false, length = 300)
    private String supplierName;
    @Column(name = "item_name", nullable = false, length = 200)
    private String itemName;
    @Column(nullable = false, precision = 60, scale = 18)
    private BigDecimal quantity;
    @Column(nullable = false, length = 50)
    private String unit;
    @Column(name = "landed_total", precision = 60, scale = 18)
    private BigDecimal landedTotal;
    @Column(nullable = false, length = 30)
    private String status;
    @Column(name = "received_quantity", precision = 60, scale = 18)
    private BigDecimal receivedQuantity;
    @Column(name = "arrival_date")
    private Instant arrivalDate;
    @Column(length = 1000)
    private String notes;
    @Version
    @Column(nullable = false)
    private long version;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected PurchaseOrder() {}

    public static PurchaseOrder derive(
            String taskId,
            String orderNo,
            String supplierName,
            String itemName,
            BigDecimal quantity,
            String unit,
            BigDecimal landedTotal) {
        var order = new PurchaseOrder();
        order.id = UUID.randomUUID().toString().replace("-", "");
        order.taskId = taskId;
        order.orderNo = orderNo;
        order.supplierName = supplierName;
        order.itemName = itemName;
        order.quantity = quantity;
        order.unit = unit;
        order.landedTotal = landedTotal;
        order.status = OrderStatus.PENDING_SHIPMENT.wireValue();
        order.createdAt = Instant.now();
        order.updatedAt = order.createdAt;
        return order;
    }

    public String getId() { return id; }
    public String getTaskId() { return taskId; }
    public String getOrderNo() { return orderNo; }
    public String getSupplierName() { return supplierName; }
    public String getItemName() { return itemName; }
    public BigDecimal getQuantity() { return quantity; }
    public String getUnit() { return unit; }
    public BigDecimal getLandedTotal() { return landedTotal; }
    public String getStatus() { return status; }
    public BigDecimal getReceivedQuantity() { return receivedQuantity; }
    public Instant getArrivalDate() { return arrivalDate; }
    public String getNotes() { return notes; }
    public long getVersion() { return version; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }

    public void ship() {
        this.status = OrderStatus.SHIPPED.wireValue();
        this.updatedAt = Instant.now();
    }

    /** Record one receipt batch and keep the aggregate quantity on the order. */
    public void receive(BigDecimal batchQuantity, Instant arrivalDate, String notes) {
        var previous = receivedQuantity == null ? BigDecimal.ZERO : receivedQuantity;
        this.receivedQuantity = previous.add(batchQuantity);
        this.status = this.receivedQuantity.compareTo(quantity) == 0
                ? OrderStatus.RECEIVED.wireValue()
                : OrderStatus.PARTIALLY_RECEIVED.wireValue();
        this.arrivalDate = arrivalDate;
        this.notes = notes;
        this.updatedAt = Instant.now();
    }

    public void close(String notes) {
        this.status = OrderStatus.CLOSED.wireValue();
        if (notes != null) {
            this.notes = notes;
        }
        this.updatedAt = Instant.now();
    }
}
