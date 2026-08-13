package com.caijiatai.procurement.supplier;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

/** 供应商档案（K1）：报价与中标记录按 supplier_name 自动关联，不建外键。 */
@Entity
@Table(name = "supplier")
public class Supplier {
    public static final String STATUS_ACTIVE = "ACTIVE";
    public static final String STATUS_PAUSED = "PAUSED";
    public static final String STATUS_BLACKLISTED = "BLACKLISTED";

    @Id
    @Column(length = 32)
    private String id;
    @Column(nullable = false, unique = true, length = 300)
    private String name;
    @Column(name = "contact_person", length = 100)
    private String contactPerson;
    @Column(length = 50)
    private String phone;
    @Column(length = 150)
    private String email;
    @Column(length = 500)
    private String address;
    @Column(name = "main_categories", length = 500)
    private String mainCategories;
    @Column(nullable = false, length = 20)
    private String status;
    @Column(length = 1000)
    private String notes;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected Supplier() {}

    public static Supplier create(
            String name, String contactPerson, String phone, String email,
            String address, String mainCategories, String notes) {
        var supplier = new Supplier();
        supplier.id = UUID.randomUUID().toString().replace("-", "");
        supplier.name = name.strip();
        supplier.contactPerson = blankToNull(contactPerson);
        supplier.phone = blankToNull(phone);
        supplier.email = blankToNull(email);
        supplier.address = blankToNull(address);
        supplier.mainCategories = blankToNull(mainCategories);
        supplier.status = STATUS_ACTIVE;
        supplier.notes = blankToNull(notes);
        supplier.createdAt = Instant.now();
        supplier.updatedAt = supplier.createdAt;
        return supplier;
    }

    public String getId() { return id; }
    public String getName() { return name; }
    public String getContactPerson() { return contactPerson; }
    public String getPhone() { return phone; }
    public String getEmail() { return email; }
    public String getAddress() { return address; }
    public String getMainCategories() { return mainCategories; }
    public String getStatus() { return status; }
    public String getNotes() { return notes; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }

    public void updateProfile(
            String contactPerson, String phone, String email,
            String address, String mainCategories, String notes) {
        this.contactPerson = blankToNull(contactPerson);
        this.phone = blankToNull(phone);
        this.email = blankToNull(email);
        this.address = blankToNull(address);
        this.mainCategories = blankToNull(mainCategories);
        this.notes = blankToNull(notes);
        this.updatedAt = Instant.now();
    }

    public void changeStatus(String status) {
        this.status = status;
        this.updatedAt = Instant.now();
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.strip();
    }
}
