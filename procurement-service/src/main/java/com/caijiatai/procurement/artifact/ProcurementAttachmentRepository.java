package com.caijiatai.procurement.artifact;

import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ProcurementAttachmentRepository extends JpaRepository<ProcurementAttachment, String> {
    List<ProcurementAttachment> findByTaskIdOrderByCreatedAtAsc(String taskId);
    Optional<ProcurementAttachment> findByTaskIdAndSha256(String taskId, String sha256);
}
