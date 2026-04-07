/**
 * @file csi_crypto.c
 * @brief AES-128-GCM encryption for CSI UDP frames.
 *
 * Uses the mbedTLS GCM API available in ESP-IDF.
 * The nonce is constructed as:
 *   counter(4B LE) || boot_rand(8B)
 * where counter increments per encrypted send and boot_rand is filled
 * once by esp_fill_random() at init time, guaranteeing per-packet
 * uniqueness within a session.
 */

#include "csi_crypto.h"

#include <string.h>
#include "esp_log.h"
#include "esp_random.h"
#include "mbedtls/gcm.h"

static const char *TAG = "csi_crypto";

static mbedtls_gcm_context s_gcm_ctx;
static int      s_initialized = 0;
static uint32_t s_counter     = 0;
static uint8_t  s_boot_rand[8];

int csi_crypto_init(const uint8_t *key, size_t key_len)
{
    if (key == NULL || key_len != 16) {
        ESP_LOGE(TAG, "Key must be exactly 16 bytes for AES-128");
        return -1;
    }

    mbedtls_gcm_init(&s_gcm_ctx);

    /* key_len * 8 = 128 bits for AES-128. */
    int ret = mbedtls_gcm_setkey(&s_gcm_ctx, MBEDTLS_CIPHER_ID_AES,
                                 key, (unsigned int)(key_len * 8));
    if (ret != 0) {
        ESP_LOGE(TAG, "mbedtls_gcm_setkey failed: -0x%04x", (unsigned int)(-ret));
        mbedtls_gcm_free(&s_gcm_ctx);
        return -1;
    }

    /* Boot-time random bytes make the nonce unique across reboots. */
    esp_fill_random(s_boot_rand, sizeof(s_boot_rand));
    s_counter = 0;
    s_initialized = 1;

    ESP_LOGI(TAG, "AES-128-GCM ready (boot_rand=%02x%02x%02x%02x...)",
             s_boot_rand[0], s_boot_rand[1], s_boot_rand[2], s_boot_rand[3]);
    return 0;
}

int csi_crypto_enabled(void)
{
    return s_initialized;
}

int csi_crypto_encrypt(
    const uint8_t *aad,    size_t aad_len,
    const uint8_t *plain,  size_t plain_len,
    uint8_t       *nonce_out,
    uint8_t       *cipher_out,
    uint8_t       *tag_out)
{
    if (!s_initialized) {
        ESP_LOGE(TAG, "csi_crypto_encrypt called before init");
        return -1;
    }

    /* Build nonce: 4-byte counter (LE) followed by 8-byte boot random. */
    uint32_t ctr = s_counter++;
    memcpy(nonce_out,     &ctr,       4);
    memcpy(nonce_out + 4, s_boot_rand, 8);

    int ret = mbedtls_gcm_crypt_and_tag(
        &s_gcm_ctx,
        MBEDTLS_GCM_ENCRYPT,
        plain_len,
        nonce_out, CSI_CRYPTO_NONCE_LEN,
        aad, aad_len,
        plain,
        cipher_out,
        CSI_CRYPTO_TAG_LEN, tag_out);

    if (ret != 0) {
        ESP_LOGE(TAG, "GCM encrypt failed: -0x%04x", (unsigned int)(-ret));
        return -1;
    }

    return 0;
}
