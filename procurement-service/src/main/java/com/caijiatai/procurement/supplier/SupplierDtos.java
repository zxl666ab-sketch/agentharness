package com.caijiatai.procurement.supplier;

/** 供应商档案 DTO（K1）。字段名沿用项目 snake_case JSON 惯例。 */
public final class SupplierDtos {
    private SupplierDtos() {}

    public record SaveRequest(
            String name,
            String contactPerson,
            String phone,
            String email,
            String address,
            String mainCategories,
            String status,
            String notes) {}

    public record Performance(
            String level,
            String score,
            String winRateScore,
            String activityScore,
            String statusScore,
            String baseScore) {}

    public record ProfileQuote(
            String quoteId,
            String taskId,
            String taskReference,
            String itemName,
            String sourceFilename,
            String createdAt) {}

    public record Profile(
            String id,
            String name,
            String contactPerson,
            String phone,
            String email,
            String address,
            String mainCategories,
            String status,
            String notes,
            String cooperationStatus,
            String quoteCount,
            String winCount,
            String winRate,
            Performance performance,
            java.util.List<String> items,
            java.util.List<ProfileQuote> recentQuotes,
            String createdAt,
            String updatedAt) {}
}
