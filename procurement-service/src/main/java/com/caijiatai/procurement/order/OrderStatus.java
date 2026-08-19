package com.caijiatai.procurement.order;

/** 采购订单状态：发货后允许分批收货，累计收满后才进入 RECEIVED。 */
public enum OrderStatus {
    PENDING_SHIPMENT,
    SHIPPED,
    PARTIALLY_RECEIVED,
    RECEIVED,
    CLOSED;

    public String wireValue() {
        return name();
    }

    public static OrderStatus fromWire(String value) {
        return OrderStatus.valueOf(value);
    }
}
