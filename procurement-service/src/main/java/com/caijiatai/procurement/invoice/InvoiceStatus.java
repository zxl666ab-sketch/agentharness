package com.caijiatai.procurement.invoice;

/** 发票状态机（P3-1，冻结设计口径扩展）：REGISTERED → MATCHED → RECONCILED；差异挂起/作废。 */
public enum InvoiceStatus {
    REGISTERED("registered"),
    MATCHED("matched"),
    DIFF_HOLD("diff_hold"),
    VOIDED("voided"),
    RECONCILED("reconciled");

    private final String wireValue;

    InvoiceStatus(String wireValue) {
        this.wireValue = wireValue;
    }

    public String wireValue() {
        return wireValue;
    }

    public static InvoiceStatus fromWire(String value) {
        for (var status : values()) {
            if (status.wireValue.equals(value)) {
                return status;
            }
        }
        throw new IllegalArgumentException("未知发票状态: " + value);
    }
}
