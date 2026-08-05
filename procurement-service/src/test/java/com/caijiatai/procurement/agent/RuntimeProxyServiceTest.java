package com.caijiatai.procurement.agent;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PipedInputStream;
import java.io.PipedOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;

class RuntimeProxyServiceTest {
    @Test
    void flushesSseChunksBeforeTheUpstreamCloses() throws Exception {
        var input = new PipedInputStream();
        var source = new PipedOutputStream(input);
        var output = new FlushCountingOutputStream();
        var worker = Thread.ofVirtual().start(() -> {
            try (input) {
                RuntimeProxyService.copyWithFlush(input, output);
            } catch (IOException error) {
                throw new IllegalStateException(error);
            }
        });

        source.write(": heartbeat\n\n".getBytes(StandardCharsets.UTF_8));
        source.flush();

        assertThat(output.flushed.await(1, TimeUnit.SECONDS)).isTrue();
        assertThat(output.toString(StandardCharsets.UTF_8)).isEqualTo(": heartbeat\n\n");
        source.close();
        worker.join(1000);
        assertThat(worker.isAlive()).isFalse();
    }

    private static final class FlushCountingOutputStream extends ByteArrayOutputStream {
        private final CountDownLatch flushed = new CountDownLatch(1);

        @Override
        public void flush() throws IOException {
            flushed.countDown();
            super.flush();
        }
    }
}
