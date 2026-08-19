package com.caijiatai.procurement.order;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.cache.InsightsCache;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.settlement.SettlementService;
import java.util.Map;
import org.junit.jupiter.api.Test;

class OrderControllerTest {
    @Test
    void listOrdersIsPureReadAndOnlyDelegatesToList() {
        var orders = mock(OrderService.class);
        var settlements = mock(SettlementService.class);
        var cache = mock(InsightsCache.class);
        var properties = mock(AppProperties.class);
        when(properties.localOperator()).thenReturn("采购员");
        var expected = Map.<String, Object>of("items", java.util.List.of(), "total", 0);
        when(orders.list(null, 0, 20)).thenReturn(expected);
        var controller = new OrderController(orders, settlements, cache, properties);

        assertThat(controller.listOrders(null, 0, 20)).isSameAs(expected);

        verify(orders).list(null, 0, 20);
        verifyNoMoreInteractions(orders);
    }
}
