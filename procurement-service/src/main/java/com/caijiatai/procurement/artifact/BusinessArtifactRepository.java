package com.caijiatai.procurement.artifact;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface BusinessArtifactRepository extends JpaRepository<BusinessArtifact, String> {
    List<BusinessArtifact> findByTaskIdOrderByCreatedAtAsc(String taskId);
}
