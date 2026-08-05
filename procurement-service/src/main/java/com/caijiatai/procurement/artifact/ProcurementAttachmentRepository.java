package com.caijiatai.procurement.artifact;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ProcurementAttachmentRepository extends JpaRepository<ProcurementAttachment, String> {
    List<ProcurementAttachment> findByTaskIdOrderByCreatedAtAsc(String taskId);
}
