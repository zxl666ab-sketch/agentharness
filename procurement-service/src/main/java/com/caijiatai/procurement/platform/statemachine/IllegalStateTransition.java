package com.caijiatai.procurement.platform.statemachine;

/**
 * 非法状态流转（冻结设计 4.2）：一律 409 Conflict，
 * 业务层负责映射为各自错误码（invalid_order_transition / invalid_settlement_transition）。
 */
public final class IllegalStateTransition extends RuntimeException {
    private final String businessId;
    private final Object from;
    private final Object event;

    public IllegalStateTransition(String businessId, Object from, Object event) {
        super("非法状态流转：businessId=" + businessId + " from=" + from + " event=" + event);
        this.businessId = businessId;
        this.from = from;
        this.event = event;
    }

    public String businessId() {
        return businessId;
    }

    public Object from() {
        return from;
    }

    public Object event() {
        return event;
    }
}
