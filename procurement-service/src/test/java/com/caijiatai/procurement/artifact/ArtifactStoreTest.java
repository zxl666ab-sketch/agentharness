package com.caijiatai.procurement.artifact;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.config.AppProperties;
import java.io.ByteArrayInputStream;
import java.net.URI;
import java.nio.file.Files;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class ArtifactStoreTest {
    @TempDir
    java.nio.file.Path temp;

    @Test
    void storesUnderTwoHashShardsAndRejectsTraversal() throws Exception {
        var repository = mock(BusinessArtifactRepository.class);
        when(repository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));
        var properties = new AppProperties(
                "采购员", temp, URI.create("http://127.0.0.1:8742"), "token",
                URI.create("http://127.0.0.1:5173"), false, new AppProperties.Outbox(500));
        var store = new ArtifactStore(properties, repository);
        var artifact = store.store(
                "procurement_original", "task", "quote.xlsx",
                "application/octet-stream", new ByteArrayInputStream("evidence".getBytes()), Map.of());

        assertThat(artifact.getId()).matches("jb[0-9a-f]{32}");
        assertThat(artifact.getLocator()).matches("java-business/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}");
        assertThat(Files.readString(store.path(artifact))).isEqualTo("evidence");
        assertThatThrownBy(() -> store.store(
                "procurement_original", "task", "../escape.xlsx",
                "application/octet-stream", new ByteArrayInputStream(new byte[] {1}), Map.of()))
                .hasMessageContaining("文件名无效");
    }
}
