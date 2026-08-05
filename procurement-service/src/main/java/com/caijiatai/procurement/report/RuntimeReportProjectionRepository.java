package com.caijiatai.procurement.report;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface RuntimeReportProjectionRepository extends JpaRepository<RuntimeReportProjection, String> {
    Optional<RuntimeReportProjection> findFirstByTaskIdOrderByCreatedAtDesc(String taskId);
}
