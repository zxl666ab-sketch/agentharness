package com.caijiatai.procurement.supplier;

import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface SupplierRepository extends JpaRepository<Supplier, String> {
    Optional<Supplier> findByName(String name);

    Page<Supplier> findByStatus(String status, Pageable pageable);

    @Query("select supplier from Supplier supplier "
            + "where lower(supplier.name) like lower(concat('%', :q, '%')) "
            + "or lower(coalesce(supplier.contactPerson, '')) like lower(concat('%', :q, '%')) "
            + "or lower(coalesce(supplier.mainCategories, '')) like lower(concat('%', :q, '%'))")
    Page<Supplier> search(@Param("q") String q, Pageable pageable);

    @Query("select supplier from Supplier supplier "
            + "where (:q is null or :q = '' "
            + "   or lower(supplier.name) like lower(concat('%', :q, '%')) "
            + "   or lower(coalesce(supplier.contactPerson, '')) like lower(concat('%', :q, '%')) "
            + "   or lower(coalesce(supplier.mainCategories, '')) like lower(concat('%', :q, '%'))) "
            + "and (:status is null or :status = '' or supplier.status = :status)")
    Page<Supplier> search(@Param("q") String q, @Param("status") String status, Pageable pageable);

    List<Supplier> findAllByOrderByNameAsc();

    long countByStatus(String status);
}
