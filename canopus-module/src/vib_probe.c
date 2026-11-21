/*
 * vib_probe.c — Canopus native module: continuous LRA excitation for
 * measuring the real resonant frequency (f0) of the Xiaomi Band 11 motor.
 *
 * Goal: the Band 11 Z-axis LRA is narrow-band; its drive frequency is locked
 * by firmware (the vibrator_run waveform segment only carries duration and
 * amplitude, never a frequency). So we cannot "sweep" from the module.
 * Instead we keep the motor steadily buzzing and let the user record the
 * sound through an empty container / resonant cavity and read f0 off an FFT.
 *
 * Activate  -> immediately start a 5000 ms pulse, then re-trigger on a
 *              periodic timer so excitation is effectively continuous.
 * Deactivate/stop -> cancel vibration and destroy the timer.
 * query     -> no-op: zero-import target cannot link the runtime's status
 *              writer helpers; excitation is verified externally (audio FFT).
 *
 * No firmware addresses are hard-coded here: all hardware access goes
 * through ctx->haptics / ctx->time services on the exact-target supervisor.
 */
#include "canopus_module_entry.h"
#include <stddef.h>

#define VIB_PULSE_MS          5000u       /* max accepted by haptics service */
#define VIB_RETRIGGER_MS      4000u       /* re-drive before the pulse ends  */
#define VIB_ERR_NO_SERVICE    (-38)       /* ENODATA-ish: service object absent */

struct vib_state {
    int32_t  timer_id;
    uint32_t ticks;
    int32_t  last_rc;
    uint32_t active;
};

static struct vib_state s_state;

/* Timer fires on the supervisor's serialized task. Re-arm a long pulse. */
static void vib_tick(const struct canopus_context_v1 *ctx, int32_t timer_id,
                     void *user_data)
{
    struct vib_state *st = (struct vib_state *)user_data;
    (void)timer_id;
    if (st == 0) return;
    if (ctx == 0 || ctx->haptics == 0 || ctx->haptics->vibrate == 0) {
        st->last_rc = VIB_ERR_NO_SERVICE;
        return;
    }
    st->last_rc = ctx->haptics->vibrate(ctx, VIB_PULSE_MS);
    st->ticks += 1u;
}

static int32_t vib_prepare(const struct canopus_context_v1 *ctx)
{
    struct vib_state *st = &s_state;
    (void)ctx;
    st->timer_id = -1;
    st->ticks = 0u;
    st->last_rc = 0;
    st->active = 0u;
    return 0;
}

static int32_t vib_activate(const struct canopus_context_v1 *ctx)
{
    struct vib_state *st = &s_state;

    if (ctx == 0 || ctx->haptics == 0 || ctx->haptics->vibrate == 0) {
        return VIB_ERR_NO_SERVICE;
    }

    /* Kick off immediately so the motor is buzzing the moment it is enabled. */
    st->last_rc = ctx->haptics->vibrate(ctx, VIB_PULSE_MS);
    st->ticks = 1u;

    /* Keep it buzzing: re-trigger on a periodic timer. The haptics service
     * serializes requests, so a new pulse simply replaces the previous one. */
    st->timer_id = -1;
    if (ctx->time != 0 && ctx->time->timer_create != 0) {
        st->timer_id = ctx->time->timer_create(ctx, VIB_RETRIGGER_MS, 1,
                                               vib_tick, st);
        if (st->timer_id >= 0 && ctx->time->timer_start != 0) {
            (void)ctx->time->timer_start(ctx, st->timer_id);
        }
    }
    st->active = 1u;
    return 0;
}

static int32_t vib_deactivate(const struct canopus_context_v1 *ctx)
{
    struct vib_state *st = &s_state;
    st->active = 0u;

    if (ctx != 0) {
        if (st->timer_id >= 0 && ctx->time != 0) {
            if (ctx->time->timer_stop != 0) {
                (void)ctx->time->timer_stop(ctx, st->timer_id);
            }
            if (ctx->time->timer_destroy != 0) {
                (void)ctx->time->timer_destroy(ctx, st->timer_id);
            }
        }
        if (ctx->haptics != 0 && ctx->haptics->cancel != 0) {
            (void)ctx->haptics->cancel(ctx);
        }
    }
    st->timer_id = -1;
    return 0;
}

static int32_t vib_stop(const struct canopus_context_v1 *ctx)
{
    return vib_deactivate(ctx);
}

static int32_t vib_query(struct canopus_status_writer_v1 *writer)
{
    /* The .179 target is zero-import: status-writer helpers live in the
     * supervisor runtime and would be undefined imports here. Keep query a
     * no-op, matching the official all-services-test module. excitation is
     * confirmed externally by the buzzing sound + recorded FFT peak. */
    (void)writer;
    return 0;
}

static const struct canopus_module_descriptor_v1 s_descriptor = {
    .struct_size    = sizeof(struct canopus_module_descriptor_v1),
    .abi_major      = CANOPUS_ABI_MAJOR,
    .abi_minor      = CANOPUS_ABI_MINOR,
    .flags          = 0u,
    .module_id      = "vib_probe",
    .module_version = "0.1.0",
    .build_id       = "vib-probe-0.1.0",
    .target_id      = CANOPUS_TARGET_ID,
    .prepare        = vib_prepare,
    .activate       = vib_activate,
    .deactivate     = vib_deactivate,
    .stop           = vib_stop,
    .query          = vib_query,
};

CANOPUS_MODULE_ENTRY(s_descriptor)
