package com.caijiatai.procurement.quote;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface QuoteCorrectionRepository extends JpaRepository<QuoteCorrection, String> {
    Page<QuoteCorrection> findAllByOrderByCreatedAtDesc(Pageable pageable);
}
