package com.caijiatai.procurement.contract;

/** 合同状态机（P3-2）：DRAFT → PENDING_APPROVAL → EFFECTIVE → EXECUTING → CLOSED + CHANGE_REQUEST。 */
public enum ContractStatus {
    DRAFT("draft"),
    PENDING_APPROVAL("pending_approval"),
    EFFECTIVE("effective"),
    EXECUTING("executing"),
    CHANGE_REQUEST("change_request"),
    CLOSED("closed");

    private final String wireValue;

    ContractStatus(String wireValue) {
        this.wireValue = wireValue;
    }

    public String wireValue() {
        return wireValue;
    }

    public static ContractStatus fromWire(String value) {
        for (var status : values()) {
            if (status.wireValue.equals(value)) {
                return status;
            }
        }
        throw new IllegalArgumentException("未知合同状态: " + value);
    }
}
