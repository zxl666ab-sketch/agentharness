package com.caijiatai.procurement.ai;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AiResultRepository extends JpaRepository<AiResult, String> {
    Optional<AiResult> findByAiTaskId(String aiTaskId);

    Optional<AiResult> findByIdAndBusinessId(String id, String businessId);
}
