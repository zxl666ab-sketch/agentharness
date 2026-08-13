package com.caijiatai.procurement.agent;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.util.LinkedHashMap;
import java.util.Map;

public final class MessageCodec {
    private MessageCodec() {}

    /** HMAC over the complete canonical envelope, excluding the detached signature. */
    public static String signEnvelope(String key, Map<String, Object> envelope) {
        var unsigned = new LinkedHashMap<>(envelope);
        unsigned.remove("signature");
        try {
            var mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return java.util.HexFormat.of().formatHex(mac.doFinal(CanonicalJson.bytes(unsigned)));
        } catch (NoSuchAlgorithmException | java.security.InvalidKeyException error) {
            throw new IllegalStateException(error);
        }
    }

    public static boolean verifyEnvelope(String key, Map<String, Object> envelope) {
        var signature = envelope.get("signature");
        if (!(signature instanceof String value) || value.isBlank()) {
            return false;
        }
        return MessageDigest.isEqual(
                signEnvelope(key, envelope).getBytes(StandardCharsets.UTF_8),
                value.getBytes(StandardCharsets.UTF_8));
    }
}
