/* SPDX-License-Identifier: AGPL-3.0-or-later
 * MIbandR vibe-player payload - Xiaomi Smart Band 11, firmware 4.100.179.
 *
 * Standalone native payload derived from the Canopus loader chain (AGPL-3.0,
 * see native/licenses/CANOPUS-AGPLv3-LICENSE). Loaded by the vendored
 * stage1/stage2 ELF loader; runs as a zero-import ELF32 ET_REL object.
 *
 * Purpose: drive the stock LRA with REAL per-segment amplitude through the
 * firmware symbol miwear_vibrator_run (0x0C6D3D31), whose 20-byte parameter
 * block accepts waveform segments of {offset u16, duration u16, flags u8,
 * amplitude u8}. This is the capability the Canopus haptics service hides
 * (its platform layer hard-codes amplitude 255).
 *
 * Exposed interface: character device /dev/vibe (same /dev prefix as the
 * proven /dev/canopus node). The device is opened with the firmware's
 * ORIGINAL io.open (recovered from the sandboxed wrapper via
 * debug.getupvalue, exactly like the Canopus installer does for
 * /dev/canopus) and accepts one command byte per write:
 *
 *   0x00  stop current vibration (vibrator_cancel)
 *   0x01  amplitude sweep: 5 levels 16/64/128/200/255, 900 ms on, 800 ms off
 *   0x02  short-pulse response: 50/100/200/400 ms at amplitude 255
 *   0x03  steady 3 s at amplitude 255 (sanity reference)
 *
 * vibrator_run is asynchronous (it returns a vibration id and the firmware
 * motor service plays the sequence), so neither the constructor nor the
 * write handler ever blocks.
 */

#include <stdint.h>
#include "canopus_band11_addresses.h"

/* ---- firmware ABI, recovered by Canopus (platform.c:1940-1969) ---- */

struct vibe_segment {
    uint16_t offset;       /* semantics unprobed; keep 0 like Canopus */
    uint16_t duration_ms;
    uint8_t  flags;        /* keep 0 like Canopus */
    uint8_t  amplitude;    /* 0..255 */
};

struct vibe_waveform {
    int32_t  count;
    const struct vibe_segment *segments;
};

struct vibe_params {
    uint8_t  type;         /* +0, keep 0 */
    uint8_t  reserved0[7];
    uint8_t  priority;     /* +8: 0 never preempts exclusive stock vibration */
    uint8_t  kind;         /* +9: 2 = waveform, data points at descriptor */
    uint8_t  reserved1[2];
    const char *key;       /* +12 */
    const void *data;      /* +16 -> struct vibe_waveform */
};

/* ---- symbol calls: numeric address + function pointer, no imports ---- */

typedef int  (*vibe_run_fn)(const struct vibe_params *params);
typedef void (*vibe_cancel_fn)(int id);
typedef int  (*register_driver_fn)(const char *path, const void *fops,
                                   void *priv);

#define VIBE_KIND_WAVEFORM  2u
#define VIBE_BUSY           (-3)  /* exclusive stock vibration playing */

/* Firmware open/close for in-kernel self-verification (matches Canopus
 * supervisor's register-then-open-verify, canopus_supervisor_platform.c:335).
 * This firmware's O_RDONLY is 1 (canopus_supervisor_platform.c:74). */
typedef int (*fw_open_fn)(const char *path, int flags);
typedef int (*fw_close_fn)(int fd);
typedef int *(*fw_errno_location_fn)(void);
#define FW_O_RDONLY 1
#define VIBE_REGISTER_VERIFY_BASE 65536  /* Canopus REGISTER_VERIFY_ERRNO_BASE */

/* Nuttx character device node, same prefix as the proven /dev/canopus. */
#define VIBE_DEVICE_PATH    "/dev/vibe"

/* ---- recovered file_operations layout (canopus_veneer.h:31-39) ---- */

struct vibe_fops {
    void   *open;          /* +0x00 */
    void   *close;         /* +0x04 */
    void   *read;          /* +0x08 */
    void   *write;         /* +0x0c */
    void   *lseek;         /* +0x10 */
    void   *ioctl;         /* +0x14 */
    uint8_t tail[24];
};

/* ---- test waveforms (static storage: zeroed .bss/.rodata, loader-backed) ---- */

static int32_t s_last_id;

/* 0x01: amplitude sweep - does segment amplitude change loudness? */
static const struct vibe_segment s_scan[9] = {
    {0u, 900u, 0u, 16u},  {0u, 800u, 0u, 0u},
    {0u, 900u, 0u, 64u},  {0u, 800u, 0u, 0u},
    {0u, 900u, 0u, 128u}, {0u, 800u, 0u, 0u},
    {0u, 900u, 0u, 200u}, {0u, 800u, 0u, 0u},
    {0u, 900u, 0u, 255u},
};

/* 0x02: shortest usable segment duration at full amplitude */
static const struct vibe_segment s_pulse[7] = {
    {0u,  50u, 0u, 255u}, {0u, 600u, 0u, 0u},
    {0u, 100u, 0u, 255u}, {0u, 600u, 0u, 0u},
    {0u, 200u, 0u, 255u}, {0u, 600u, 0u, 0u},
    {0u, 400u, 0u, 255u},
};

/* 0x03: steady reference tone */
static const struct vibe_segment s_steady[1] = {
    {0u, 3000u, 0u, 255u},
};

static struct vibe_waveform s_wave;
static struct vibe_params  s_params; /* static: zero-initialized */

static int32_t vibe_play(const struct vibe_segment *segments, uint32_t count)
{
    vibe_run_fn run = (vibe_run_fn)(uintptr_t)CANOPUS_SUP_VIBRATOR_RUN;
    int id;

    s_wave.count = (int32_t)count;
    s_wave.segments = segments;
    s_params.type = 0u;
    s_params.priority = 0u;
    s_params.kind = VIBE_KIND_WAVEFORM;
    s_params.key = 0;
    s_params.data = (const void *)&s_wave;

    id = run(&s_params);
    if (id == VIBE_BUSY) {
        return -16;                 /* -EBUSY */
    }
    if (id < 0) {
        return (int32_t)id;
    }
    s_last_id = (int32_t)id;
    return (int32_t)count;
}

static void vibe_stop(void)
{
    vibe_cancel_fn cancel = (vibe_cancel_fn)(uintptr_t)CANOPUS_SUP_VIBRATOR_CANCEL;
    if (s_last_id > 0) {
        cancel(s_last_id);
        s_last_id = 0;
    }
}

static int32_t vibe_write(void *filep, const void *buffer, uint32_t count)
{
    const uint8_t *cmd = (const uint8_t *)buffer;

    (void)filep;
    if (buffer == 0 || count < 1u) {
        return -22;                 /* -EINVAL */
    }
    switch (cmd[0]) {
    case 0x00u:
        vibe_stop();
        return (int32_t)count;
    case 0x01u:
        return vibe_play(s_scan, 9u);
    case 0x02u:
        return vibe_play(s_pulse, 7u);
    case 0x03u:
        return vibe_play(s_steady, 1u);
    default:
        return -22;
    }
}

static int32_t vibe_read(void *filep, void *buffer, uint32_t count)
{
    /* Device is write-only control; report EOF like an empty pipe. */
    (void)filep;
    (void)buffer;
    (void)count;
    return 0;
}

static int vibe_open(void *filep)
{
    (void)filep;
    return 0;
}

static int vibe_close(void *filep)
{
    (void)filep;
    return 0;
}

/* Every callback non-NULL, mirroring the proven Canopus fops
 * (s_fops/s_installer_fops always set open/close/read/write). */
static const struct vibe_fops s_fops = {
    (void *)(uintptr_t)&vibe_open,
    (void *)(uintptr_t)&vibe_close,
    (void *)(uintptr_t)&vibe_read,
    (void *)(uintptr_t)&vibe_write,
    0, 0,
    {0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u,
     0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u},
};

/* Constructor runs on the firmware exec task's stack (stage2 invokes the
 * .init_array entries with r0 = mailbox). The stage2 wrapper expects the
 * constructor to publish its result: 0 on success, negative errno otherwise.
 * Mirrors canopus_supervisor_module.c canopus_sup_ctor_mailbox. */
static void vibe_player_ctor(volatile int32_t *mailbox)
{
    register_driver_fn reg = (register_driver_fn)(uintptr_t)B11_REGISTER_DRIVER;
    fw_open_fn fw_open = (fw_open_fn)(uintptr_t)FW_NUTTX_OPEN;
    fw_close_fn fw_close = (fw_close_fn)(uintptr_t)FW_NUTTX_CLOSE;
    int rc;

    s_last_id = 0;
    rc = reg(VIBE_DEVICE_PATH, (const void *)&s_fops, (void *)0);
    if (rc != 0 && rc != -17) {
        /* -EEXIST tolerated: a stale same-name inode from an earlier load
         * serves the same device. Anything else is a real failure. */
        *mailbox = rc;
        __asm__ volatile("dsb sy" ::: "memory");
        return;
    }
    /* The firmware register_driver wrapper discards inode_reserve failures
     * and returns the inode-lock release result (Canopus supervisor comment),
     * so verify the node is actually openable before reporting success. */
    {
        fw_errno_location_fn errno_location =
            (fw_errno_location_fn)(uintptr_t)CANOPUS_SUP_NUTTX_ERRNO_LOCATION;
        int fd = fw_open(VIBE_DEVICE_PATH, FW_O_RDONLY);
        if (fd < 0) {
            /* The firmware open returns -1 with the reason in errno
             * (canopus_supervisor_platform.c:340 reads errno_location). */
            int *error_pointer = errno_location ? errno_location() : (int *)0;
            int err = error_pointer != 0 ? *error_pointer : 0;
            if (err < 0) err = -err;
            if (err == 0) err = -fd;
            if (err > 4095) err = 4095;
            *mailbox = -(VIBE_REGISTER_VERIFY_BASE + err);
            __asm__ volatile("dsb sy" ::: "memory");
            return;
        }
        (void)fw_close(fd);
    }
    *mailbox = 0;
    __asm__ volatile("dsb sy" ::: "memory");
}

__attribute__((used, section(".init_array")))
static void (*const vibe_player_init_entry)(volatile int32_t *) = vibe_player_ctor;
