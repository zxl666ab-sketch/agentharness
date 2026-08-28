package com.caijiatai.procurement.api;

import static org.assertj.core.api.Assertions.assertThat;

import com.caijiatai.procurement.config.AppProperties;
import java.net.URI;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.ConfigDataApplicationContextInitializer;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/**
 * J-M5 来源门禁（GATE-1 修正）：业务写 = loopback OR APP_TRUSTED_NETWORKS CIDR；
 * 空配置退化为 loopback-only；Host/Origin/dev-vite 校验保持不变。
 */
class RequestBoundaryFilterTest {

    private static RequestBoundaryFilter filter(String trustedNetworks) {
        return filter(trustedNetworks, false);
    }

    private static RequestBoundaryFilter filter(String trustedNetworks, boolean developmentMode) {
        var properties = new AppProperties(
                null, null, URI.create("http://127.0.0.1:5173"), developmentMode, null,
                "kafka", null, "0123456789abcdef0123456789abcdef");
        return new RequestBoundaryFilter(properties, trustedNetworks);
    }

    private static MockHttpServletRequest write(String remoteAddr) {
        var request = new MockHttpServletRequest("POST", "/api/procurement/orders/x/pay");
        request.setRemoteAddr(remoteAddr);
        request.setServerName("localhost");
        return request;
    }

    private static MockFilterChain apply(RequestBoundaryFilter filter, MockHttpServletRequest request)
            throws Exception {
        var chain = new MockFilterChain();
        filter.doFilter(request, new MockHttpServletResponse(), chain);
        return chain;
    }

    @Test
    void loopbackWritesPassTheSourceGate() throws Exception {
        // 127/8 全段、::1 的两种 Java 拼写、IPv4-mapped 回环都必须放行（裸机默认配置）。
        assertThat(apply(filter(""), write("127.0.0.1")).getRequest()).isNotNull();
        assertThat(apply(filter(""), write("127.9.9.9")).getRequest()).isNotNull();
        assertThat(apply(filter(""), write("::1")).getRequest()).isNotNull();
        assertThat(apply(filter(""), write("0:0:0:0:0:0:0:1")).getRequest()).isNotNull();
        assertThat(apply(filter(""), write("0:0:0:0:0:ffff:127.0.0.1")).getRequest()).isNotNull();
    }

    @Test
    void offBridgeWritesAreForbiddenWhenNoTrustedNetworksConfigured() throws Exception {
        var response = new MockHttpServletResponse();
        var chain = new MockFilterChain();
        filter("").doFilter(write("172.21.0.6"), response, chain);
        assertThat(chain.getRequest()).isNull();
        assertThat(response.getStatus()).isEqualTo(403);
        assertThat(response.getErrorMessage()).contains("write_source_not_trusted");
    }

    @Test
    void dockerNatWritesPassWhenBridgeCidrIsTrusted() throws Exception {
        // 容器拓扑：compose 注入 APP_TRUSTED_NETWORKS=172.16.0.0/12。
        var filter = filter("172.16.0.0/12, 192.168.5.7 ,broken,/33,10.0.0.0/8");
        assertThat(apply(filter, write("172.21.0.6")).getRequest()).isNotNull();
        assertThat(apply(filter, write("0:0:0:0:0:ffff:172.21.0.6")).getRequest()).isNotNull();
        assertThat(apply(filter, write("192.168.5.7")).getRequest()).isNotNull();
        assertThat(apply(filter, write("10.1.2.3")).getRequest()).isNotNull();
        // 网段外与无效条目（broken//33 → 丢弃，不影响其余项）
        assertThat(apply(filter, write("192.168.5.8")).getRequest()).isNull();
        assertThat(apply(filter, write("8.8.8.8")).getRequest()).isNull();
    }

    @Test
    void ipv6CidrsAreSupported() throws Exception {
        var filter = filter("fd00::/8");
        assertThat(apply(filter, write("fd12::34")).getRequest()).isNotNull();
        assertThat(apply(filter, write("fe12::34")).getRequest()).isNull();
        // IPv6 网络不匹配 IPv4 源
        assertThat(apply(filter, write("172.21.0.6")).getRequest()).isNull();
    }

    @Test
    void malformedEntriesAreDroppedWithoutFailingStartup() throws Exception {
        var filter = filter("garbage,1.2.3,10.0.0.0/99,,not:an:host");
        assertThat(apply(filter, write("10.1.2.3")).getRequest()).isNull();
        assertThat(apply(filter, write("127.0.0.1")).getRequest()).isNotNull();
    }

    @Test
    void safeMethodsAreUnaffectedByTheSourceGate() throws Exception {
        var request = new MockHttpServletRequest("GET", "/api/health");
        request.setRemoteAddr("172.21.0.6");
        request.setServerName("localhost");
        assertThat(apply(filter(""), request).getRequest()).isNotNull();
    }

    @Test
    void hostCheckRemainsTheDnsRebindingDefense() throws Exception {
        var request = write("127.0.0.1");
        request.setServerName("evil.example.com");
        var response = new MockHttpServletResponse();
        filter("172.16.0.0/12").doFilter(request, response, new MockFilterChain());
        assertThat(response.getStatus()).isEqualTo(400);
        assertThat(response.getErrorMessage()).contains("invalid_host");
    }

    @Test
    void originCheckStillGuardsTrustedSources() throws Exception {
        var request = write("172.21.0.6");
        request.addHeader("Origin", "http://evil.example.com:9");
        var response = new MockHttpServletResponse();
        filter("172.16.0.0/12").doFilter(request, response, new MockFilterChain());
        assertThat(response.getStatus()).isEqualTo(403);
        assertThat(response.getErrorMessage()).contains("origin_not_allowed");
    }

    @Test
    void devModeViteOriginLogicIsIntact() throws Exception {
        var request = write("172.21.0.6");
        request.addHeader("Origin", "http://127.0.0.1:5173");
        assertThat(apply(filter("172.16.0.0/12", true), request).getRequest()).isNotNull();
    }

    // ---- package-private static helper coverage (GATE-1 coordination contract) ----

    @Test
    void cidrParsingMatchesBridgeTopology() {
        var networks = RequestBoundaryFilter.parseTrustedNetworks(
                "172.16.0.0/12, 192.168.5.7 ,broken,/33,10.0.0.0/8");
        assertThat(RequestBoundaryFilter.matchesTrustedNetwork(networks, "172.21.0.6")).isTrue();
        assertThat(RequestBoundaryFilter.matchesTrustedNetwork(
                networks, "0:0:0:0:0:ffff:172.21.0.6")).isTrue();
        assertThat(RequestBoundaryFilter.matchesTrustedNetwork(networks, "192.168.5.7")).isTrue();
        assertThat(RequestBoundaryFilter.matchesTrustedNetwork(networks, "192.168.5.8")).isFalse();
        assertThat(RequestBoundaryFilter.matchesTrustedNetwork(networks, "10.1.2.3")).isTrue();
        assertThat(RequestBoundaryFilter.matchesTrustedNetwork(networks, "8.8.8.8")).isFalse();
        // ::1 是回环但不在 IPv4 网络内（来源门禁与回环判定互相独立）
        assertThat(RequestBoundaryFilter.matchesTrustedNetwork(networks, "::1")).isFalse();
        assertThat(RequestBoundaryFilter.matchesTrustedNetwork(java.util.List.of(), "172.21.0.6"))
                .isFalse();
    }

    @Test
    void emptyAndGarbageTrustedNetworksYieldNoEntries() {
        assertThat(RequestBoundaryFilter.parseTrustedNetworks(null)).isEmpty();
        assertThat(RequestBoundaryFilter.parseTrustedNetworks("  ")).isEmpty();
        // IPv4-only 无效条目被丢弃；但 "::2" 是合法 IPv6 /128（InetAddress 匹配器支持 v6）
        assertThat(RequestBoundaryFilter.parseTrustedNetworks("garbage,1.2.3")).isEmpty();
        var v6 = RequestBoundaryFilter.parseTrustedNetworks("::2");
        assertThat(RequestBoundaryFilter.matchesTrustedNetwork(v6, "::2")).isTrue();
        assertThat(RequestBoundaryFilter.matchesTrustedNetwork(v6, "::3")).isFalse();
    }

    @Test
    void appTrustedNetworksEnvPropertyWiresIntoTheFilterBean() throws Exception {
        // GATE-1：compose 以 APP_TRUSTED_NETWORKS=172.16.0.0/12 注入；application.properties
        // 的 app.trusted-networks=${APP_TRUSTED_NETWORKS:} 占位符必须把同名环境变量解析进
        // @Value 参数，且产出的 Bean 真实放行 Docker 网桥来源的写请求。
        new ApplicationContextRunner()
                .withInitializer(new ConfigDataApplicationContextInitializer())
                .withUserConfiguration(WiringTestConfiguration.class)
                .withPropertyValues(
                        "app.internal-hmac-key=0123456789abcdef0123456789abcdef",
                        "APP_TRUSTED_NETWORKS=172.16.0.0/12")
                .run(ran -> {
                    assertThat(ran).hasNotFailed();
                    assertThat(ran.getEnvironment().getProperty("app.trusted-networks"))
                            .isEqualTo("172.16.0.0/12");
                    var filter = ran.getBean(RequestBoundaryFilter.class);
                    var response = new MockHttpServletResponse();
                    var chain = new MockFilterChain();
                    filter.doFilter(write("172.21.0.6"), response, chain);
                    assertThat(chain.getRequest()).isNotNull();
                    assertThat(response.getStatus()).isEqualTo(200);
                    var blocked = new MockFilterChain();
                    filter.doFilter(write("8.8.8.8"), new MockHttpServletResponse(), blocked);
                    assertThat(blocked.getRequest()).isNull();
                });
    }

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(AppProperties.class)
    @Import(RequestBoundaryFilter.class)
    static class WiringTestConfiguration {}
}
