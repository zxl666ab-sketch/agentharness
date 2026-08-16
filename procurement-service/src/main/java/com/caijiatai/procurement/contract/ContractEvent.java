package com.caijiatai.procurement.contract;

/** 合同状态机事件（P3-2）。 */
public enum ContractEvent {
    SUBMIT,
    APPROVE,
    REJECT,
    EXECUTE,
    CLOSE,
    REQUEST_CHANGE
}
