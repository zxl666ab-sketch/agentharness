package com.caijiatai.procurement.order;

/** 订单流转事件（冻结设计 4.3）：ship / receive / close。 */
public enum OrderEvent {
    /** 待发货 → 已发货。 */
    SHIP,
    /** 已发货 → 已收货（必填 received_quantity 与 arrival_date，超收拒绝）。 */
    RECEIVE,
    /** 待发货→已关闭=取消（不派生对账单）；已收货→已关闭=完成（对账单已派生）。 */
    CLOSE;

    public static OrderEvent fromAction(String action) {
        return OrderEvent.valueOf(action.strip().toUpperCase(java.util.Locale.ROOT));
    }
}
