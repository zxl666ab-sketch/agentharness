package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.config.AppProperties;
import java.net.http.HttpClient;
import java.time.Duration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

@Configuration(proxyBeanMethods = false)
public class AgentHttpConfiguration {
    @Bean
    RestClient agentRestClient(RestClient.Builder builder, AppProperties properties) {
        var httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
        var requestFactory = new JdkClientHttpRequestFactory(httpClient);
        requestFactory.setReadTimeout(Duration.ofSeconds(30));
        return builder
                .requestFactory(requestFactory)
                .baseUrl(properties.agentBaseUrl().toString())
                .build();
    }
}
