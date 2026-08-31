package com.caijiatai.procurement.supplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;

import com.caijiatai.procurement.cache.InsightsCache;
import java.lang.reflect.Method;
import java.util.Arrays;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ResponseStatus;

class SupplierControllerTest {
    /**
     * 回归：OpenAPI 契约冻结 DELETE /suppliers/{id} → 204（无响应体）。
     * 曾返回 200 空 body，前端 requestJson 走 JSON 解析抛
     * "Unexpected end of JSON input"：删除成功却报失败、列表不刷新。
     */
    @Test
    void deleteIsDeclaredNoContentPerOpenApiContract() throws Exception {
        var method = Arrays.stream(SupplierController.class.getDeclaredMethods())
                .filter(item -> "delete".equals(item.getName()))
                .findFirst()
                .orElseThrow();
        var annotation = method.getAnnotation(ResponseStatus.class);
        assertThat(annotation).as("DELETE 必须显式声明响应码").isNotNull();
        assertThat(annotation.value()).isEqualTo(HttpStatus.NO_CONTENT);
        assertThat(method.getReturnType()).isEqualTo(void.class);
    }

    @Test
    void deleteEvictsInsightsCacheAfterServiceDelete() {
        var service = mock(SupplierService.class);
        var cache = mock(InsightsCache.class);
        var controller = new SupplierController(service, cache);

        controller.delete("s1");

        verify(service).delete("s1");
        verify(cache).evictAll();
        verifyNoMoreInteractions(service, cache);
    }
}
