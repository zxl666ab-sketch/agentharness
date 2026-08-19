package com.caijiatai.procurement.order;

/** 订单流转事件；完整收货由服务根据累计数量选择内部 COMPLETE 事件。 */
public enum OrderEvent {
    /** 待发货 → 已发货。 */
    SHIP,
    /** 已发货/部分收货 → 部分收货。 */
    RECEIVE,
    /** 已发货/部分收货 → 已收货；仅累计数量等于订单量时使用。 */
    RECEIVE_COMPLETE,
    /** 待发货→已关闭=取消（不派生对账单）；已收货→已关闭=完成（对账单已派生）。 */
    CLOSE;

    public static OrderEvent fromAction(String action) {
        return switch (action == null ? "" : action.strip().toLowerCase(java.util.Locale.ROOT)) {
            case "ship" -> SHIP;
            case "receive" -> RECEIVE;
            case "close" -> CLOSE;
            default -> throw new IllegalArgumentException("unsupported order action");
        };
    }
}
