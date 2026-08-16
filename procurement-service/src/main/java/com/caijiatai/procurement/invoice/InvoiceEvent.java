package com.caijiatai.procurement.invoice;

/** 发票状态机事件（P3-1）。 */
public enum InvoiceEvent {
    MATCH,
    HOLD,
    VOID,
    FORCE_MATCH,
    RECONCILE
}
