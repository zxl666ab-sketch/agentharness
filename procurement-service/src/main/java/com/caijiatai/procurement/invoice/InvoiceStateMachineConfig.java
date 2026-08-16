package com.caijiatai.procurement.invoice;

import com.caijiatai.procurement.platform.statemachine.StateMachine;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * 发票状态机定义（P3-1）：REGISTERED → MATCHED → RECONCILED，差异挂起 DIFF_HOLD、
 * 作废 VOIDED；在 OrderStateMachineConfig 的注册表中注册（复用注册式引擎）。
 */
@Configuration
public class InvoiceStateMachineConfig {
    public static final String INVOICE_MACHINE = "invoice";

    @Bean
    public StateMachine<InvoiceStatus, InvoiceEvent> invoiceStateMachine() {
        return StateMachine.define(InvoiceStatus.class, InvoiceEvent.class)
                .permit(InvoiceStatus.REGISTERED, InvoiceEvent.MATCH, InvoiceStatus.MATCHED)
                .permit(InvoiceStatus.REGISTERED, InvoiceEvent.HOLD, InvoiceStatus.DIFF_HOLD)
                .permit(InvoiceStatus.REGISTERED, InvoiceEvent.VOID, InvoiceStatus.VOIDED)
                .permit(InvoiceStatus.DIFF_HOLD, InvoiceEvent.MATCH, InvoiceStatus.MATCHED)
                .permit(InvoiceStatus.DIFF_HOLD, InvoiceEvent.FORCE_MATCH, InvoiceStatus.MATCHED)
                .permit(InvoiceStatus.DIFF_HOLD, InvoiceEvent.VOID, InvoiceStatus.VOIDED)
                .permit(InvoiceStatus.MATCHED, InvoiceEvent.RECONCILE, InvoiceStatus.RECONCILED)
                .build();
    }
}
