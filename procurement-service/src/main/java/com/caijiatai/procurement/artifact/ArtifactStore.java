package com.caijiatai.procurement.artifact;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.config.AppProperties;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ArtifactStore {
    private final Path root;
    private final BusinessArtifactRepository repository;

    public ArtifactStore(AppProperties properties, BusinessArtifactRepository repository) {
        this.root = properties.artifactRoot().toAbsolutePath().normalize();
        this.repository = repository;
    }

    @Transactional
    public BusinessArtifact store(
            String kind,
            String taskId,
            String filename,
            String contentType,
            InputStream source,
            Map<String, Object> metadata) {
        validateFilename(filename);
        Path temporary = null;
        try {
            Files.createDirectories(root);
            temporary = Files.createTempFile(root, ".artifact-", ".tmp");
            var digest = MessageDigest.getInstance("SHA-256");
            long size;
            try (var input = new DigestInputStream(source, digest)) {
                size = Files.copy(input, temporary, StandardCopyOption.REPLACE_EXISTING);
            }
            var sha256 = HexFormat.of().formatHex(digest.digest());
            var locator = "java-business/" + sha256.substring(0, 2) + "/"
                    + sha256.substring(2, 4) + "/" + sha256;
            var destination = resolveLocator(locator);
            Files.createDirectories(destination.getParent());
            if (!Files.exists(destination)) {
                atomicMove(temporary, destination);
                temporary = null;
            }
            return repository.save(BusinessArtifact.create(
                    kind,
                    taskId,
                    sha256,
                    locator,
                    filename,
                    contentType == null || contentType.isBlank() ? "application/octet-stream" : contentType,
                    size,
                    metadata));
        } catch (IOException | NoSuchAlgorithmException error) {
            throw new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, "artifact_store_failed", "业务文件保存失败");
        } finally {
            if (temporary != null) {
                try {
                    Files.deleteIfExists(temporary);
                } catch (IOException ignored) {
                    // The temporary file remains under the configured store root and is never addressable.
                }
            }
        }
    }

    public Path path(BusinessArtifact artifact) {
        if (!"java-business".equals(artifact.getOwnerPrefix())) {
            throw new ApiException(HttpStatus.NOT_FOUND, "artifact_not_found", "未找到业务文件");
        }
        var path = resolveLocator(artifact.getLocator());
        if (!Files.isRegularFile(path)) {
            throw new ApiException(HttpStatus.NOT_FOUND, "artifact_not_found", "未找到业务文件");
        }
        return path;
    }

    private Path resolveLocator(String locator) {
        if (!locator.matches("java-business/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}")) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "invalid_artifact_locator", "业务文件定位符无效");
        }
        var relative = locator.substring("java-business/".length());
        var path = root.resolve(relative).normalize();
        if (!path.startsWith(root)) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "invalid_artifact_locator", "业务文件路径越界");
        }
        return path;
    }

    private void validateFilename(String filename) {
        if (filename == null || filename.isBlank() || filename.length() > 255
                || !Path.of(filename).getFileName().toString().equals(filename)) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "invalid_filename", "文件名无效");
        }
    }

    private void atomicMove(Path temporary, Path destination) throws IOException {
        try {
            Files.move(temporary, destination, StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException error) {
            Files.move(temporary, destination);
        }
    }
}
