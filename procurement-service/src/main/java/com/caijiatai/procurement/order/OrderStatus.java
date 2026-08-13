package com.caijiatai.procurement.order;

/** 采购订单状态（冻结设计 4.3）：PENDING_SHIPMENT --ship--> SHIPPED --receive--> RECEIVED --close--> CLOSED。 */
public enum OrderStatus {
    PENDING_SHIPMENT,
    SHIPPED,
    RECEIVED,
    CLOSED;

    public String wireValue() {
        return name();
    }

    public static OrderStatus fromWire(String value) {
        return OrderStatus.valueOf(value);
    }
}
