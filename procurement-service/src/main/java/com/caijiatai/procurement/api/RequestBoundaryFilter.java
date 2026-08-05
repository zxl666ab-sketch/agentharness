package com.caijiatai.procurement.api;

import com.caijiatai.procurement.config.AppProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.net.URI;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpMethod;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public final class RequestBoundaryFilter extends OncePerRequestFilter {
    private static final Set<String> SAFE_METHODS = Set.of("GET", "HEAD", "OPTIONS");
    private static final Set<String> LOCAL_HOSTS = Set.of("127.0.0.1", "localhost", "[::1]");
    private final AppProperties properties;

    public RequestBoundaryFilter(AppProperties properties) {
        this.properties = properties;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        var requestId = UUID.randomUUID().toString();
        request.setAttribute(ApiExceptionHandler.REQUEST_ID_ATTRIBUTE, requestId);
        response.setHeader("X-Request-Id", requestId);

        if (request.getRequestURI().startsWith("/internal/v1/")) {
            if (!constantTimeEquals(
                    properties.agentInternalToken(), request.getHeader("X-Agent-Internal-Token"))) {
                response.sendError(HttpServletResponse.SC_UNAUTHORIZED);
                return;
            }
            filterChain.doFilter(request, response);
            return;
        }

        var host = request.getServerName().toLowerCase(Locale.ROOT);
        if (!LOCAL_HOSTS.contains(host)) {
            response.sendError(HttpServletResponse.SC_BAD_REQUEST, "invalid_host");
            return;
        }
        if (!SAFE_METHODS.contains(request.getMethod()) && !sameOrigin(request)) {
            response.sendError(HttpServletResponse.SC_FORBIDDEN, "origin_not_allowed");
            return;
        }
        filterChain.doFilter(request, response);
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

    private boolean constantTimeEquals(String expected, String actual) {
        if (actual == null || expected.length() != actual.length()) {
            return false;
        }
        int result = 0;
        for (int index = 0; index < expected.length(); index++) {
            result |= expected.charAt(index) ^ actual.charAt(index);
        }
        return result == 0;
    }
}
