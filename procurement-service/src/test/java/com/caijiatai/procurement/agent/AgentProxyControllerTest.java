package com.caijiatai.procurement.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.caijiatai.procurement.api.ApiException;
import org.junit.jupiter.api.Test;

class AgentProxyControllerTest {
    @Test
    void streamCursorUsesLastEventIdWhenQueryCursorIsAbsent() {
        assertThat(AgentProxyController.streamCursor(null, "42")).isEqualTo(42L);
        assertThat(AgentProxyController.streamCursor("", "42")).isEqualTo(42L);
    }

    @Test
    void streamCursorPrefersExplicitQueryCursor() {
        assertThat(AgentProxyController.streamCursor("7", "42")).isEqualTo(7L);
        assertThat(AgentProxyController.streamCursor(null, null)).isZero();
    }

    @Test
    void streamCursorRejectsInvalidOrNegativeValues() {
        assertThatThrownBy(() -> AgentProxyController.streamCursor("not-a-number", null))
                .isInstanceOf(ApiException.class);
        assertThatThrownBy(() -> AgentProxyController.streamCursor(null, "-1"))
                .isInstanceOf(ApiException.class);
    }
}
