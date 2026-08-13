package com.caijiatai.procurement.settlement;

/** 对账单状态（冻结设计 4.3）：UNSETTLED --settle--> SETTLED --pay--> PAID。 */
public enum SettlementStatus {
    UNSETTLED,
    SETTLED,
    PAID;

    public String wireValue() {
        return name();
    }

    public static SettlementStatus fromWire(String value) {
        return SettlementStatus.valueOf(value);
    }
}
