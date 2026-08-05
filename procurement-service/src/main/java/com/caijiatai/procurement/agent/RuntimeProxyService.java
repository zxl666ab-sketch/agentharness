package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.config.AppProperties;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.util.Set;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Service
public final class RuntimeProxyService {
    private static final Set<String> RESPONSE_HEADERS = Set.of(
            "content-type", "content-disposition", "cache-control", "etag", "last-modified");
    private final RestClient rest;
    private final URI baseUri;
    private final String token;

    public RuntimeProxyService(RestClient agentRestClient, AppProperties properties) {
        this.baseUri = properties.agentBaseUrl();
        this.rest = agentRestClient;
        this.token = properties.agentInternalToken();
    }

    public ResponseEntity<byte[]> get(String path) {
        return exchange(HttpMethod.GET, path, null);
    }

    public ResponseEntity<byte[]> post(String path, byte[] body) {
        return exchange(HttpMethod.POST, path, body == null ? new byte[0] : body);
    }

    public void stream(String query, String lastEventId, HttpServletResponse response) {
        var suffix = query == null || query.isBlank() ? "" : "?" + query;
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) baseUri.resolve("/api/stream" + suffix).toURL().openConnection();
            connection.setRequestMethod("GET");
            connection.setConnectTimeout(3_000);
            connection.setReadTimeout(30 * 60 * 1_000);
            connection.setRequestProperty("Accept", "text/event-stream");
            connection.setRequestProperty("X-Agent-Internal-Token", token);
            if (lastEventId != null && !lastEventId.isBlank()) {
                connection.setRequestProperty("Last-Event-ID", lastEventId);
            }
            var status = connection.getResponseCode();
            if (status >= 500) {
                connection.disconnect();
                throw unavailable();
            }
            var upstream = connection;
            var input = status >= 400 ? upstream.getErrorStream() : upstream.getInputStream();
            response.setStatus(status);
            response.setContentType(org.springframework.http.MediaType.TEXT_EVENT_STREAM_VALUE);
            response.setHeader(HttpHeaders.CACHE_CONTROL, "no-cache");
            try (input) {
                copyWithFlush(input, response.getOutputStream());
            } finally {
                upstream.disconnect();
            }
        } catch (IOException error) {
            if (connection != null) {
                connection.disconnect();
            }
            if (response.isCommitted()) {
                return;
            }
            throw unavailable();
        }
    }

    static void copyWithFlush(InputStream input, OutputStream output) throws IOException {
        var buffer = new byte[8192];
        int first;
        while ((first = input.read()) != -1) {
            output.write(first);
            while (input.available() > 0) {
                var read = input.read(buffer, 0, Math.min(buffer.length, input.available()));
                if (read == -1) {
                    break;
                }
                output.write(buffer, 0, read);
            }
            output.flush();
        }
    }

    private ResponseEntity<byte[]> exchange(HttpMethod method, String path, byte[] body) {
        try {
            return rest.method(method)
                    .uri(path)
                    .header("X-Agent-Internal-Token", token)
                    .headers(headers -> {
                        if (body != null) {
                            headers.setContentType(org.springframework.http.MediaType.APPLICATION_JSON);
                        }
                    })
                    .body(body == null ? new byte[0] : body)
                    .exchange((request, response) -> {
                        var headers = new HttpHeaders();
                        response.getHeaders().forEach((name, values) -> {
                            if (RESPONSE_HEADERS.contains(name.toLowerCase())) {
                                headers.put(name, values);
                            }
                        });
                        return new ResponseEntity<>(response.getBody().readAllBytes(), headers, response.getStatusCode());
                    });
        } catch (RestClientException error) {
            throw unavailable();
        }
    }

    private ApiException unavailable() {
        return new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "agent_unavailable", "Python Agent 暂时不可用");
    }
}
