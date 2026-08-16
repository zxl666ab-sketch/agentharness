package com.caijiatai.procurement.contract;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * P3-2 合同草拟一致性校验（Java 确定性）：草拟文本中的金额/交期必须与注入字段一致，
 * 不一致时后端拦截并要求人工确认（Agent 只提供草拟文本，不一致判断归 Java）。
 */
public final class ContractConsistencyPolicy {

    private static final Pattern AMOUNT = Pattern.compile(
            "(?:金额|总价|总金额|合同金额|价税合计)[^0-9¥￥.]{0,12}[¥￥]?\\s*([0-9]+(?:\\.[0-9]+)?)");
    private static final Pattern AMOUNT_YUAN = Pattern.compile(
            "[¥￥]?\\s*([0-9]+(?:\\.[0-9]+)?)\\s*元");
    private static final Pattern LEAD_DAYS = Pattern.compile(
            "(?:交期|交货期|交付期|交期要求|最迟交付)[^0-9]{0,12}([0-9]{1,4})\\s*(?:天|个?工作日|个?自然日)");
    private static final Pattern LEAD_DAYS_IN = Pattern.compile(
            "([0-9]{1,4})\\s*(?:天|个?工作日|个?自然日)\\s*内(?:交货|交付|到货|完成)");

    private ContractConsistencyPolicy() {}

    public static Map<String, Object> check(String draftText, BigDecimal amount, int leadDays) {
        String text = draftText == null ? "" : draftText;
        String amountInText = extractAmount(text);
        String leadDaysInText = extractLeadDays(text);
        boolean amountMatches = amountInText != null && amount.compareTo(new BigDecimal(amountInText)) == 0;
        boolean leadDaysMatches = leadDaysInText != null && leadDays == Integer.parseInt(leadDaysInText);
        var value = new LinkedHashMap<String, Object>();
        value.put("amount_in_text", amountInText);
        value.put("lead_days_in_text", leadDaysInText);
        value.put("amount_matches", amountMatches);
        value.put("lead_days_matches", leadDaysMatches);
        value.put("consistent", amountMatches && leadDaysMatches);
        return value;
    }

    static String extractAmount(String text) {
        Matcher matcher = AMOUNT.matcher(text);
        if (matcher.find()) {
            return normalize(matcher.group(1));
        }
        matcher = AMOUNT_YUAN.matcher(text);
        if (matcher.find()) {
            return normalize(matcher.group(1));
        }
        return null;
    }

    static String extractLeadDays(String text) {
        Matcher matcher = LEAD_DAYS.matcher(text);
        if (matcher.find()) {
            return matcher.group(1);
        }
        matcher = LEAD_DAYS_IN.matcher(text);
        if (matcher.find()) {
            return matcher.group(1);
        }
        return null;
    }

    private static String normalize(String raw) {
        return new BigDecimal(raw).stripTrailingZeros().toPlainString();
    }
}
