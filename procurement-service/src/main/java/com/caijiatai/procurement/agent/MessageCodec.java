package com.caijiatai.procurement.agent;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

public final class MessageCodec {
    private MessageCodec() {}

    /** HMAC-SHA256 over the message identity fields (cross-language deterministic). */
    public static String sign(String key, String operationId, String payloadSha256, String kind) {
        try {
            var mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            var content = (kind + "\n" + operationId + "\n" + payloadSha256).getBytes(StandardCharsets.UTF_8);
            return java.util.HexFormat.of().formatHex(mac.doFinal(content));
        } catch (NoSuchAlgorithmException | java.security.InvalidKeyException error) {
            throw new IllegalStateException(error);
        }
    }

    public static boolean verify(String key, String operationId, String payloadSha256, String kind, String signature) {
        if (signature == null || signature.isBlank()) {
            return false;
        }
        return MessageDigest.isEqual(
                sign(key, operationId, payloadSha256, kind).getBytes(StandardCharsets.UTF_8),
                signature.getBytes(StandardCharsets.UTF_8));
    }
}
