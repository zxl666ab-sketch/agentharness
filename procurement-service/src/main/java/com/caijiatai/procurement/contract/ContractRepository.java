package com.caijiatai.procurement.contract;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface ContractRepository extends JpaRepository<Contract, String> {
    Page<Contract> findAllByOrderByCreatedAtDesc(Pageable pageable);

    Page<Contract> findByStatusOrderByCreatedAtDesc(String status, Pageable pageable);

    Page<Contract> findByTaskIdOrderByCreatedAtDesc(String taskId, Pageable pageable);

    List<Contract> findByTaskId(String taskId);

    Optional<Contract> findByContractNo(String contractNo);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select contract from Contract contract where contract.id = :id")
    Optional<Contract> lockById(@Param("id") String id);
}
