package com.caijiatai.procurement.artifact;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.agent.RuntimeProxyService;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public final class ArtifactController {
    private final BusinessArtifactRepository artifacts;
    private final ArtifactStore store;
    private final RuntimeProxyService runtime;

    public ArtifactController(
            BusinessArtifactRepository artifacts,
            ArtifactStore store,
            RuntimeProxyService runtime) {
        this.artifacts = artifacts;
        this.store = store;
        this.runtime = runtime;
    }

    @GetMapping("/api/artifacts/{artifactId}")
    public ResponseEntity<?> metadata(@PathVariable String artifactId) {
        if (artifactId.matches("py[0-9a-f]{32}")) {
            return runtime.get("/api/artifacts/" + artifactId.substring(2));
        }
        return ResponseEntity.ok(metadata(load(artifactId)));
    }

    @GetMapping("/api/artifacts/{artifactId}/raw")
    public ResponseEntity<?> raw(@PathVariable String artifactId) {
        if (artifactId.matches("py[0-9a-f]{32}")) {
            return runtime.get("/api/artifacts/" + artifactId.substring(2) + "/raw");
        }
        return rawResponse(load(artifactId));
    }

    @GetMapping("/internal/v1/artifacts/{artifactId}/raw")
    public ResponseEntity<FileSystemResource> internalRaw(@PathVariable String artifactId) {
        return rawResponse(load(artifactId));
    }

    private BusinessArtifact load(String artifactId) {
        if (!artifactId.matches("jb[0-9a-f]{32}")) {
            throw new ApiException(org.springframework.http.HttpStatus.NOT_FOUND, "artifact_owner_unknown", "Artifact 所有者前缀无效");
        }
        return artifacts.findById(artifactId)
                .orElseThrow(() -> new ApiException(org.springframework.http.HttpStatus.NOT_FOUND, "artifact_not_found", "未找到业务文件"));
    }

    private ResponseEntity<FileSystemResource> rawResponse(BusinessArtifact artifact) {
        var disposition = ContentDisposition.attachment()
                .filename(artifact.getFilename(), java.nio.charset.StandardCharsets.UTF_8)
                .build();
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType(artifact.getContentType()))
                .contentLength(artifact.getSizeBytes())
                .header(HttpHeaders.CONTENT_DISPOSITION, disposition.toString())
                .header("X-Artifact-Owner", artifact.getOwnerPrefix())
                .header("X-Content-SHA256", artifact.getSha256())
                .body(new FileSystemResource(store.path(artifact)));
    }

    private Map<String, Object> metadata(BusinessArtifact artifact) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", artifact.getId());
        value.put("owner", artifact.getOwnerPrefix());
        value.put("kind", artifact.getKind());
        value.put("task_id", artifact.getTaskId());
        value.put("sha256", artifact.getSha256());
        value.put("filename", artifact.getFilename());
        value.put("content_type", artifact.getContentType());
        value.put("size_bytes", artifact.getSizeBytes());
        value.put("metadata", artifact.getMetadata());
        value.put("created_at", artifact.getCreatedAt());
        return value;
    }
}
