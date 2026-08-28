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
 * Kafka 消费端 DLQ：瞬时错误退避重试（J-M3：2s×3，最坏 ~6s/条，避免毒批在
 * max.poll.interval.ms 内触发再均衡风暴），超出后投递到 {@code <topic>.dlq}；
 * 校验失败/409 由业务代码直接终态。
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
        // J-M3: 5s×5 retries × max.poll.records=10 ≈ 255s per poison batch, dangerously
        // close to the old 300s max.poll.interval.ms (rebalance loop risk). 2s×3 keeps the
        // worst case ≈ 66s per batch; max.poll.interval.ms is also raised to 600s.
        var errorHandler = new DefaultErrorHandler(recoverer, new FixedBackOff(2_000L, 3));
        factory.setCommonErrorHandler(errorHandler);
        return factory;
    }
}
