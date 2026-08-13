package com.caijiatai.procurement.api;

import com.caijiatai.procurement.agent.RuntimeEventRepository;
import java.util.LinkedHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicBoolean;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.MediaType;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import tools.jackson.databind.ObjectMapper;

@Service
public final class EventStreamService {
    private final RuntimeEventRepository events;
    private final ObjectMapper mapper;
    private final Semaphore connectionPermits = new Semaphore(32);

    public EventStreamService(RuntimeEventRepository events, ObjectMapper mapper) {
        this.events = events;
        this.mapper = mapper;
    }

    public SseEmitter stream(long after) {
        if (!connectionPermits.tryAcquire()) {
            throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "event_stream_limit", "实时事件连接数已达上限");
        }
        var emitter = new SseEmitter(0L);
        var released = new AtomicBoolean();
        Runnable release = () -> {
            if (released.compareAndSet(false, true)) connectionPermits.release();
        };
        var executor = Executors.newSingleThreadScheduledExecutor(runnable -> {
            var thread = new Thread(runnable, "runtime-event-stream");
            thread.setDaemon(true);
            return thread;
        });
        var cursor = new AtomicLong(after);
        executor.scheduleWithFixedDelay(() -> {
            try {
                var rows = events.findByGlobalSeqGreaterThanOrderByGlobalSeqAsc(cursor.get(), PageRequest.of(0, 100));
                if (rows.isEmpty()) {
                    emitter.send(SseEmitter.event().comment("heartbeat"));
                    return;
                }
                for (var row : rows) {
                    var data = new LinkedHashMap<String, Object>();
                    data.put("event_id", String.valueOf(row.getId()));
                    data.put("global_seq", row.getGlobalSeq());
                    data.put("run_seq", 0);
                    data.put("session_id", "");
                    data.put("run_id", row.getRunId() == null ? "" : row.getRunId());
                    data.put("type", row.getType());
                    data.put("timestamp", row.getOccurredAt().toString());
                    data.put("payload", row.getPayload());
                    emitter.send(SseEmitter.event()
                            .id(String.valueOf(row.getGlobalSeq()))
                            .name(row.getType())
                            .data(mapper.writeValueAsBytes(data), MediaType.APPLICATION_JSON));
                    cursor.set(row.getGlobalSeq());
                }
            } catch (Exception error) {
                emitter.completeWithError(error);
                executor.shutdownNow();
            }
        }, 0, 500, TimeUnit.MILLISECONDS);
        emitter.onCompletion(() -> { release.run(); executor.shutdownNow(); });
        emitter.onTimeout(() -> { release.run(); executor.shutdownNow(); });
        emitter.onError(error -> { release.run(); executor.shutdownNow(); });
        return emitter;
    }
}
