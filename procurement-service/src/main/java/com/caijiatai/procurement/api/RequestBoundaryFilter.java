package com.caijiatai.procurement.api;

import com.caijiatai.procurement.config.AppProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.net.InetAddress;
import java.net.URI;
import java.net.UnknownHostException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * 请求边界过滤器：Host 校验（DNS-rebinding 防线）+ 同源校验 + 业务写来源门禁。
 *
 * <p>信任边界（J-M5 修正 / GATE-1）：容器拓扑下 Java 以 {@code APP_BIND_ADDRESS=0.0.0.0}
 * 绑定在容器内，浏览器流量经 Docker NAT 到达，TCP 对端是网桥地址（如 172.17.x.x）而非
 * loopback，纯回环判定会拒绝一切合法写入。业务写因此要求 remoteAddr 为 loopback，或命中
 * {@code APP_TRUSTED_NETWORKS}（逗号分隔 IPv4/IPv6 CIDR，经 {@code app.trusted-networks}
 * 以 @Value 绑定；为空时退化为 loopback-only，即裸机部署的正确默认）。远端边界由
 * bind-address + 端口映射 + 本来源门禁共同封闭；同一台机器上的本地进程按设计始终处于
 * 信任边界之内（单用户本地应用）。Host 头校验保持不变，作为浏览器场景的 DNS-rebinding
 * 防线（浏览器无法伪造 TCP 源地址，但可以带着任意 Host 回来）。
 */
@Component
public final class RequestBoundaryFilter extends OncePerRequestFilter {
    private static final Logger log = LoggerFactory.getLogger(RequestBoundaryFilter.class);
    private static final Set<String> SAFE_METHODS = Set.of("GET", "HEAD", "OPTIONS");
    private static final Set<String> LOCAL_HOSTS = Set.of("127.0.0.1", "localhost", "[::1]");
    private final AppProperties properties;
    private final List<TrustedNetwork> trustedNetworks;

    public RequestBoundaryFilter(
            AppProperties properties,
            @Value("${app.trusted-networks:}") String trustedNetworks) {
        this.properties = properties;
        this.trustedNetworks = parseTrustedNetworks(trustedNetworks);
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        var requestId = UUID.randomUUID().toString();
        request.setAttribute(ApiExceptionHandler.REQUEST_ID_ATTRIBUTE, requestId);
        response.setHeader("X-Request-Id", requestId);

        if (request.getHeader("Forwarded") != null
                || request.getHeader("X-Forwarded-Host") != null
                || request.getHeader("X-Forwarded-Proto") != null) {
            response.sendError(HttpServletResponse.SC_BAD_REQUEST, "forwarded_headers_not_allowed");
            return;
        }
        var host = request.getServerName().toLowerCase(Locale.ROOT);
        if (!LOCAL_HOSTS.contains(host)) {
            // Unchanged DNS-rebinding defense (kept deliberately separate from the
            // source-address gate below).
            response.sendError(HttpServletResponse.SC_BAD_REQUEST, "invalid_host");
            return;
        }
        if (!SAFE_METHODS.contains(request.getMethod())) {
            // J-M5: business writes require the TCP peer to be loopback or a source
            // inside APP_TRUSTED_NETWORKS (the Docker bridge CIDR in the containerized
            // topology). An unset/empty list keeps bare-metal runs loopback-only.
            if (!isLoopbackAddress(request.getRemoteAddr())
                    && !isTrustedNetwork(request.getRemoteAddr())) {
                response.sendError(HttpServletResponse.SC_FORBIDDEN, "write_source_not_trusted");
                return;
            }
            if (!sameOrigin(request)) {
                response.sendError(HttpServletResponse.SC_FORBIDDEN, "origin_not_allowed");
                return;
            }
        }
        filterChain.doFilter(request, response);
    }

    private static boolean isLoopbackAddress(String address) {
        if (address == null || address.isBlank()) {
            return false;
        }
        var value = address.strip();
        // IPv4 127.0.0.0/8, the IPv6 ::1 spellings Java's getRemoteAddr may render, and
        // IPv4-mapped IPv6 loopback ("0:0:0:0:0:ffff:127.x.x.y") on dual-stack sockets.
        return value.startsWith("127.")
                || value.startsWith("0:0:0:0:0:ffff:127.")
                || value.equals("::1")
                || value.equals("0:0:0:0:0:0:0:1")
                || value.equals("0:0:0:0:0:0:1");
    }

    /** Parsed source gate entry: network bytes (host bits zeroed, IPv4-normalized) + prefix. */
    record TrustedNetwork(byte[] address, int prefixLength) {}

    private boolean isTrustedNetwork(String address) {
        return matchesTrustedNetwork(trustedNetworks, address);
    }

    /** Package-private for RequestBoundaryFilterTest (GATE-1 coordination contract). */
    static boolean matchesTrustedNetwork(List<TrustedNetwork> networks, String address) {
        if (networks.isEmpty() || address == null || address.isBlank()) {
            return false;
        }
        var literal = address.strip();
        if (!isNumericHostLiteral(literal)) {
            return false;
        }
        try {
            // Numeric literals only: this never triggers a DNS lookup.
            var target = normalize(InetAddress.getByName(literal).getAddress());
            for (var network : networks) {
                if (matches(network, target)) {
                    return true;
                }
            }
            return false;
        } catch (UnknownHostException error) {
            return false;
        }
    }

    private static boolean matches(TrustedNetwork network, byte[] address) {
        if (network.address().length != address.length) {
            return false;
        }
        int wholeBytes = network.prefixLength() / 8;
        int tailBits = network.prefixLength() % 8;
        for (int index = 0; index < wholeBytes; index++) {
            if (address[index] != network.address()[index]) {
                return false;
            }
        }
        if (tailBits == 0) {
            return true;
        }
        int mask = (0xFF << (8 - tailBits)) & 0xFF;
        return (address[wholeBytes] & mask) == (network.address()[wholeBytes] & mask);
    }

    /**
     * Parses comma-separated IPv4/IPv6 CIDRs (bare addresses default to /32 or /128).
     * Malformed entries are warned and dropped; an empty result means loopback-only.
     * Package-private for RequestBoundaryFilterTest (GATE-1 coordination contract).
     */
    static List<TrustedNetwork> parseTrustedNetworks(String raw) {
        if (raw == null || raw.isBlank()) {
            return List.of();
        }
        var result = new ArrayList<TrustedNetwork>();
        for (var token : raw.split(",")) {
            var entry = token.strip();
            if (entry.isEmpty()) {
                continue;
            }
            try {
                result.add(parseTrustedNetwork(entry));
            } catch (IllegalArgumentException | UnknownHostException error) {
                log.warn("忽略无效的 APP_TRUSTED_NETWORKS 条目：{}", entry);
            }
        }
        return List.copyOf(result);
    }

    private static TrustedNetwork parseTrustedNetwork(String entry)
            throws IllegalArgumentException, UnknownHostException {
        int slash = entry.indexOf('/');
        var hostPart = (slash < 0 ? entry : entry.substring(0, slash)).strip();
        if (!isNumericHostLiteral(hostPart)) {
            throw new IllegalArgumentException("不是数字 IP 字面量：" + hostPart);
        }
        var address = normalize(InetAddress.getByName(hostPart).getAddress());
        int fullLength = address.length * 8;
        int prefixLength = fullLength;
        if (slash >= 0) {
            prefixLength = Integer.parseInt(entry.substring(slash + 1).strip());
            if (prefixLength < 0 || prefixLength > fullLength) {
                throw new IllegalArgumentException("前缀长度超出范围：" + entry);
            }
        }
        var masked = address.clone();
        int wholeBytes = prefixLength / 8;
        int tailBits = prefixLength % 8;
        if (tailBits != 0) {
            int mask = (0xFF << (8 - tailBits)) & 0xFF;
            masked[wholeBytes] = (byte) (masked[wholeBytes] & mask);
            wholeBytes++;
        }
        Arrays.fill(masked, wholeBytes, masked.length, (byte) 0);
        return new TrustedNetwork(masked, prefixLength);
    }

    /**
     * Numeric IPv4 dotted-quad or IPv6 colon literal. Guards InetAddress.getByName so a
     * misconfigured entry or hostile-looking token can never trigger hostname resolution.
     */
    private static boolean isNumericHostLiteral(String value) {
        if (value == null || value.isEmpty()) {
            return false;
        }
        if (value.indexOf(':') >= 0) {
            return value.chars().allMatch(character ->
                    "0123456789abcdefABCDEF:%.".indexOf(character) >= 0);
        }
        var octets = value.split("\\.", -1);
        if (octets.length != 4) {
            return false;
        }
        for (var octet : octets) {
            if (octet.isEmpty() || octet.length() > 3 || !octet.chars().allMatch(Character::isDigit)) {
                return false;
            }
            if (Integer.parseInt(octet) > 255) {
                return false;
            }
        }
        return true;
    }

    /** Collapses IPv4-mapped IPv6 ("::ffff:a.b.c.d", dual-stack sockets) to 4-byte IPv4. */
    private static byte[] normalize(byte[] raw) {
        if (raw.length == 16 && raw[10] == (byte) 0xFF && raw[11] == (byte) 0xFF) {
            var mapped = true;
            for (int index = 0; index < 10; index++) {
                if (raw[index] != 0) {
                    mapped = false;
                    break;
                }
            }
            if (mapped) {
                return Arrays.copyOfRange(raw, 12, 16);
            }
        }
        return raw;
    }

    private boolean sameOrigin(HttpServletRequest request) {
        var origin = request.getHeader("Origin");
        if (origin == null || origin.isBlank()) {
            return true;
        }
        URI value;
        try {
            value = URI.create(origin);
        } catch (IllegalArgumentException error) {
            return false;
        }
        var expectedPort = request.getServerPort();
        var actualPort = value.getPort() == -1 ? defaultPort(value.getScheme()) : value.getPort();
        var local = LOCAL_HOSTS.contains(value.getHost() == null ? "" : value.getHost().toLowerCase(Locale.ROOT))
                && actualPort == expectedPort;
        return local || (properties.developmentMode() && value.equals(properties.allowedViteOrigin()));
    }

    private int defaultPort(String scheme) {
        return "https".equalsIgnoreCase(scheme) ? 443 : 80;
    }
}
