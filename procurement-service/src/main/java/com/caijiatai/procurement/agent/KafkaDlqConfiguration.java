package com.caijiatai.procurement.agent;

import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.common.TopicPartition;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.ConcurrentKafkaListenerContainerFactory;
import org.springframework.kafka.core.ConsumerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.FixedBackOff;

/**
 * Kafka 消费端 DLQ：瞬时错误退避重试（5s/30s/2min 级别，最多 5 次），
 * 超出后投递到 {@code <topic>.dlq}；校验失败/409 由业务代码直接终态。
 */
@Configuration
@ConditionalOnProperty(prefix = "app", name = "agent-mode", havingValue = "kafka")
public class KafkaDlqConfiguration {

    @Bean
    ConcurrentKafkaListenerContainerFactory<String, byte[]> kafkaListenerContainerFactory(
            ConsumerFactory<String, byte[]> consumerFactory,
            KafkaTemplate<String, byte[]> template) {
        var factory = new ConcurrentKafkaListenerContainerFactory<String, byte[]>();
        factory.setConsumerFactory(consumerFactory);
        var recoverer = new DeadLetterPublishingRecoverer(template,
                (ConsumerRecord<?, ?> record, Exception error) ->
                        new TopicPartition(record.topic() + ".dlq", record.partition()));
        var errorHandler = new DefaultErrorHandler(recoverer, new FixedBackOff(5_000L, 5));
        factory.setCommonErrorHandler(errorHandler);
        return factory;
    }
}
