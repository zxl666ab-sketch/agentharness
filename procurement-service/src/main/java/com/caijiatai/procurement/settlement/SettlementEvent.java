package com.caijiatai.procurement.settlement;

/** 对账流转事件（冻结设计 4.3）：settle / pay。 */
public enum SettlementEvent {
    /** 未对账 → 已对账。 */
    SETTLE,
    /** 已对账 → 已付款（必填 paid_at）。 */
    PAY;

    public static SettlementEvent fromAction(String action) {
        return SettlementEvent.valueOf(action.strip().toUpperCase(java.util.Locale.ROOT));
    }
}
