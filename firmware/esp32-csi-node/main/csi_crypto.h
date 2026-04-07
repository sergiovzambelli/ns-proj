/**
 * @file csi_crypto.h
 * @brief AES-128-GCM encryption for CSI UDP frames (ADR-018-enc).
 *
 * Provides confidentiality and integrity for ADR-018 frames transmitted over UDP.
 * The encrypted frame format (ADR-018-enc) uses a new magic number so receivers
 * can distinguish it from plaintext frames:
 *
 *   [Magic 0xC5110003 LE (4B)] [NodeID (1B)] [Nonce (12B)]
 *   [Ciphertext (N B)] [GCM Tag (16B)]
 *
 * AAD (authenticated, not encrypted): bytes 0-4 (magic + node_id).
 * Nonce: 4-byte counter (LE) || 8-byte boot-time random.
 * Key: AES-128 (16 bytes), provisioned via NVS key "aes_key".
 */

#ifndef CSI_CRYPTO_H
#define CSI_CRYPTO_H

#include <stdint.h>
#include <stddef.h>

/** Magic number for encrypted ADR-018-enc frames (little-endian). */
#define CSI_ENC_MAGIC        0xC5110003U

/** AES-GCM nonce length in bytes. */
#define CSI_CRYPTO_NONCE_LEN 12

/** AES-GCM authentication tag length in bytes. */
#define CSI_CRYPTO_TAG_LEN   16

/**
 * Initialize AES-128-GCM with a 16-byte key.
 *
 * Fills 8 bytes of boot-random nonce material via esp_fill_random().
 * Must be called once after NVS config is loaded.
 *
 * @param key     16-byte AES-128 key.
 * @param key_len Must be 16.
 * @return 0 on success, -1 on error.
 */
int csi_crypto_init(const uint8_t *key, size_t key_len);

/**
 * Encrypt one ADR-018 frame payload using AES-128-GCM.
 *
 * The caller assembles the encrypted frame as:
 *   aad[0..4] | nonce_out[0..11] | cipher_out[0..plain_len-1] | tag_out[0..15]
 *
 * @param aad        Additional authenticated data (new magic + node_id, 5 bytes).
 * @param aad_len    Must be 5.
 * @param plain      Plaintext bytes: original ADR-018 frame starting at byte 5.
 * @param plain_len  Length of plaintext (frame_len - 5).
 * @param nonce_out  Caller-allocated 12-byte buffer; receives the nonce.
 * @param cipher_out Caller-allocated plain_len-byte buffer; receives ciphertext.
 * @param tag_out    Caller-allocated 16-byte buffer; receives GCM tag.
 * @return 0 on success, -1 on error.
 */
int csi_crypto_encrypt(
    const uint8_t *aad,    size_t aad_len,
    const uint8_t *plain,  size_t plain_len,
    uint8_t       *nonce_out,
    uint8_t       *cipher_out,
    uint8_t       *tag_out);

/**
 * @return 1 if a key has been loaded and encryption is active, 0 otherwise.
 */
int csi_crypto_enabled(void);

#endif /* CSI_CRYPTO_H */
