package com.caijiatai.procurement.order;

import com.caijiatai.procurement.contract.ContractEvent;
import com.caijiatai.procurement.contract.ContractStateMachineConfig;
import com.caijiatai.procurement.contract.ContractStatus;
import com.caijiatai.procurement.invoice.InvoiceEvent;
import com.caijiatai.procurement.invoice.InvoiceStateMachineConfig;
import com.caijiatai.procurement.invoice.InvoiceStatus;
import com.caijiatai.procurement.platform.statemachine.StateMachine;
import com.caijiatai.procurement.platform.statemachine.StateMachineRegistry;
import com.caijiatai.procurement.settlement.SettlementEvent;
import com.caijiatai.procurement.settlement.SettlementStatus;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * 订单/对账状态机定义（冻结设计 4.2/4.3）：注册式声明合法流转，引擎统一校验。
 * P3-1：发票状态机在同一平台注册表中注册。
 */
@Configuration
public class OrderStateMachineConfig {
    public static final String ORDER_MACHINE = "order";
    public static final String SETTLEMENT_MACHINE = "settlement";

    @Bean
    public StateMachine<OrderStatus, OrderEvent> orderStateMachine() {
        return StateMachine.define(OrderStatus.class, OrderEvent.class)
                .permit(OrderStatus.PENDING_SHIPMENT, OrderEvent.SHIP, OrderStatus.SHIPPED)
                .permit(OrderStatus.SHIPPED, OrderEvent.RECEIVE, OrderStatus.PARTIALLY_RECEIVED)
                .permit(OrderStatus.PARTIALLY_RECEIVED, OrderEvent.RECEIVE, OrderStatus.PARTIALLY_RECEIVED)
                .permit(OrderStatus.SHIPPED, OrderEvent.RECEIVE_COMPLETE, OrderStatus.RECEIVED)
                .permit(OrderStatus.PARTIALLY_RECEIVED, OrderEvent.RECEIVE_COMPLETE, OrderStatus.RECEIVED)
                .permit(OrderStatus.PENDING_SHIPMENT, OrderEvent.CLOSE, OrderStatus.CLOSED)   // 取消
                .permit(OrderStatus.RECEIVED, OrderEvent.CLOSE, OrderStatus.CLOSED)           // 完成
                .build();
    }

    @Bean
    public StateMachine<SettlementStatus, SettlementEvent> settlementStateMachine() {
        return StateMachine.define(SettlementStatus.class, SettlementEvent.class)
                .permit(SettlementStatus.UNSETTLED, SettlementEvent.SETTLE, SettlementStatus.SETTLED)
                .permit(SettlementStatus.SETTLED, SettlementEvent.PAY, SettlementStatus.PAID)
                .build();
    }

    /** 注册到平台注册表（平台叙事：新业务注册自己的状态机即可复用引擎）。 */
    @Bean
    public StateMachineRegistry stateMachineRegistry(
            StateMachine<OrderStatus, OrderEvent> orderMachine,
            StateMachine<SettlementStatus, SettlementEvent> settlementMachine,
            StateMachine<InvoiceStatus, InvoiceEvent> invoiceMachine,
            StateMachine<ContractStatus, ContractEvent> contractMachine) {
        var registry = new StateMachineRegistry();
        registry.register(ORDER_MACHINE, orderMachine);
        registry.register(SETTLEMENT_MACHINE, settlementMachine);
        registry.register(InvoiceStateMachineConfig.INVOICE_MACHINE, invoiceMachine);
        registry.register(ContractStateMachineConfig.CONTRACT_MACHINE, contractMachine);
        return registry;
    }
}
