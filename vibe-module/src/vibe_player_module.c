/*
 * vibe_player_module.c — MIbandR Vibe Player as a Canopus module.
 *
 * Appears as "MIbandR Vibe" in the Band 11 app list: the module registers a
 * UI entry through ctx->ui->set_entry (native-ui-lab pattern, portable
 * canopus_ui API) and publishes the launcher entry through
 * ctx->launcher->register_app with page_count = 0, which binds the launcher
 * page to the module's UI entry (ADR-CAN-011 single-page contract).
 *
 * The UI offers four LRA waveform tests. Unlike the supervisor haptics
 * service (which hard-codes amplitude 255), the tests call the firmware
 * vibrator_run symbol directly — CANOPUS_SUP_VIBRATOR_RUN from the exact
 * target's generated address header — so every segment carries its REAL
 * amplitude. That is the whole point of this module: verify that segment
 * amplitude changes perceived loudness, the capability needed for
 * amplitude-modulated "audio through the motor".
 *
 * vibrator_run is asynchronous (returns a vibration id; the firmware motor
 * service plays the sequence), so no callback ever blocks.
 */
#include "canopus_module_entry.h"
#include "canopus_ui.h"

#define MODULE_ID  "vibe_player"

enum { CMD_SWEEP = 1, CMD_PULSE, CMD_STEADY, CMD_STOP };
enum { KEY_SWEEP = 10, KEY_PULSE, KEY_STEADY, KEY_STOP, KEY_STATUS };

/* ---- firmware vibrator ABI, recovered by Canopus (platform.c:1940-1969) ---- */

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

typedef int  (*vibe_run_fn)(const struct vibe_params *params);
typedef void (*vibe_cancel_fn)(int id);

#define VIBE_KIND_WAVEFORM 2u
#define VIBE_BUSY (-3)  /* exclusive stock vibration playing */

/* 0x01: amplitude sweep - does segment amplitude change loudness? */
static const struct vibe_segment s_scan[9] = {
    {0u, 900u, 0u, 16u},  {0u, 800u, 0u, 0u},
    {0u, 900u, 0u, 64u},  {0u, 800u, 0u, 0u},
    {0u, 900u, 0u, 128u}, {0u, 800u, 0u, 0u},
    {0u, 900u, 0u, 200u}, {0u, 800u, 0u, 0u},
    {0u, 900u, 0u, 255u},
};

/* 0x02: short-pulse response at full amplitude */
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
static int32_t s_last_id;
static const struct canopus_context_v1 *s_ctx;
static const char *s_result = "点击按钮开始测试";
static int32_t s_result_rc;

static int32_t vibe_play(const struct vibe_segment *segments, uint32_t count)
{
    vibe_run_fn run = (vibe_run_fn)(uintptr_t)CANOPUS_SUP_VIBRATOR_RUN;
    int32_t id;

    s_wave.count = (int32_t)count;
    s_wave.segments = segments;
    s_params.type = 0u;
    s_params.priority = 0u;
    s_params.kind = VIBE_KIND_WAVEFORM;
    s_params.key = 0;
    s_params.data = (const void *)&s_wave;

    id = run(&s_params);
    if (id == VIBE_BUSY) return -16;    /* -EBUSY */
    if (id < 0) return id;
    s_last_id = id;
    return (int32_t)count;
}

static void vibe_stop_now(void)
{
    vibe_cancel_fn cancel = (vibe_cancel_fn)(uintptr_t)CANOPUS_SUP_VIBRATOR_CANCEL;
    if (s_last_id > 0) {
        cancel(s_last_id);
        s_last_id = 0;
    }
}

/* ---- portable UI (native-ui-lab pattern) ---- */

static uint32_t vlen(const char *s) { uint32_t n = 0; while (s[n]) n++; return n; }

static int32_t ui_action(struct canopus_ui_tree_v1 *t, uint32_t key,
                         const char *title, const char *detail, uint32_t event)
{
    struct canopus_ui_action_row_props_v1 p = {
        sizeof(p), title, vlen(title), detail, vlen(detail), event, 1u };
    return s_ctx->ui->action_row(t, key, &p);
}

static int32_t ui_status(struct canopus_ui_tree_v1 *t)
{
    struct canopus_ui_text_props_v1 p = {
        sizeof(p), s_result, vlen(s_result), CANOPUS_UI_TEXT_BODY };
    return s_ctx->ui->text(t, KEY_STATUS, &p);
}

static int32_t vibe_render(struct canopus_ui_context_v1 *ui)
{
    struct canopus_ui_tree_v1 *t;
    struct canopus_ui_navigation_page_props_v1 page = {
        sizeof(page), "MIbandR Vibe", vlen("MIbandR Vibe") };
    int32_t rc = s_ctx->ui->tree_begin(ui, &t);

    if (rc != 0) return rc;
    if (rc == 0) rc = s_ctx->ui->navigation_page(t, 1u, &page);
    if (rc == 0) rc = ui_action(t, KEY_SWEEP, "扫幅度",
                                "16/64/128/200/255 递增", CMD_SWEEP);
    if (rc == 0) rc = ui_action(t, KEY_PULSE, "短脉冲",
                                "50/100/200/400 ms 全幅度", CMD_PULSE);
    if (rc == 0) rc = ui_action(t, KEY_STEADY, "恒振",
                                "3 秒 全幅度 参考音", CMD_STEADY);
    if (rc == 0) rc = ui_action(t, KEY_STOP, "停止",
                                "cancel 当前振动", CMD_STOP);
    if (rc == 0) rc = ui_status(t);
    if (rc == 0) rc = s_ctx->ui->end(t);
    return rc == 0 ? s_ctx->ui->tree_commit(t) : s_ctx->ui->tree_abort(t);
}

static int32_t vibe_on_open(const struct canopus_context_v1 *ctx,
                            struct canopus_ui_context_v1 *ui, void *user)
{
    (void)user;
    s_ctx = ctx;
    return vibe_render(ui);
}

static int32_t vibe_on_event(const struct canopus_context_v1 *ctx,
                             struct canopus_ui_context_v1 *ui,
                             const struct canopus_ui_event_v1 *e, void *user)
{
    int32_t rc;
    (void)user;
    if (e->type != CANOPUS_UI_EVENT_ACTIVATE) return 0;

    switch (e->event_id) {
    case CMD_SWEEP:  rc = vibe_play(s_scan, 9u);   break;
    case CMD_PULSE:  rc = vibe_play(s_pulse, 7u);  break;
    case CMD_STEADY: rc = vibe_play(s_steady, 1u); break;
    case CMD_STOP:
        vibe_stop_now();
        rc = 0;
        break;
    default:
        return CANOPUS_UI_ERR_ARGUMENT;
    }
    s_result_rc = rc;
    return vibe_render(ui);
}

static int32_t vibe_on_back(const struct canopus_context_v1 *ctx,
                            struct canopus_ui_context_v1 *ui, void *user)
{
    (void)ctx; (void)ui; (void)user;
    return 1; /* default: close the page */
}

static void vibe_on_close(const struct canopus_context_v1 *ctx, void *user)
{
    (void)ctx; (void)user;
}

static const struct canopus_ui_entry_v1 s_ui_entry = {
    sizeof(s_ui_entry), "MIbandR Vibe",
    vibe_on_open, vibe_on_event, vibe_on_back, vibe_on_close,
    0, 0, 0, 0,
};

/* ---- launcher publication (ADR-CAN-011 single-page contract) ---- */

static const char s_app_label[] = "MIbandR Vibe";
static const char s_app_icon[] = "/data/canopus/manager_icon.bin";

static int32_t vibe_publish_native_app_stage(const struct canopus_context_v1 *ctx,
                                             uint32_t stage)
{
    if (stage == 1u) {
        struct canopus_app_entry_v1 entry;
        int32_t app_id;
        if (ctx->launcher == 0 || ctx->launcher->register_app == 0) {
            return CANOPUS_SVC_ERR_UNSUPPORTED;
        }
        /* page_count = 0: the launcher page binds this module's UI entry. */
        entry.struct_size = sizeof(entry);
        entry.label = s_app_label;
        entry.icon = s_app_icon;
        entry.pages = 0;
        entry.page_count = 0u;
        app_id = ctx->launcher->register_app(ctx, &entry);
        return app_id < 0 ? app_id : 0;
    }
    if (stage == 2u) return 0; /* no auto-navigation; the user opens the app */
    return CANOPUS_SVC_ERR_ARGUMENT;
}

/* ---- module lifecycle ---- */

static int32_t vibe_prepare(const struct canopus_context_v1 *ctx)
{
    s_ctx = ctx;
    s_last_id = 0;
    s_result_rc = 0;
    return 0;
}

static int32_t vibe_activate(const struct canopus_context_v1 *ctx)
{
    s_ctx = ctx;
    if (ctx->ui == 0 || ctx->ui->set_entry == 0) {
        return CANOPUS_SVC_ERR_UNSUPPORTED;
    }
    return ctx->ui->set_entry(ctx, &s_ui_entry);
}

static int32_t vibe_deactivate(const struct canopus_context_v1 *ctx)
{
    if (ctx != 0 && ctx->ui != 0 && ctx->ui->set_entry != 0) {
        (void)ctx->ui->set_entry(ctx, 0);
    }
    return 0;
}

static int32_t vibe_stop(const struct canopus_context_v1 *ctx)
{
    vibe_stop_now();
    return vibe_deactivate(ctx);
}

static int32_t vibe_query(struct canopus_status_writer_v1 *writer)
{
    /* zero-import target: status-writer helpers live in the supervisor
     * runtime and would be undefined imports here; keep the no-op that the
     * official all-services-test module uses. */
    (void)writer;
    return 0;
}

static const struct canopus_module_descriptor_v1 s_descriptor = {
    .struct_size    = sizeof(struct canopus_module_descriptor_v1),
    .abi_major      = CANOPUS_ABI_MAJOR,
    .abi_minor      = CANOPUS_ABI_MINOR,
    .flags          = CANOPUS_FLAG_HAS_NATIVE_APP |
                      CANOPUS_FLAG_REGISTERS_LAUNCHER_ENTRY |
                      CANOPUS_FLAG_APP_UNREGISTER_REBOOT_REQUIRED,
    .module_id      = MODULE_ID,
    .module_version = "1.0.0",
    .build_id       = "vibe-player-1.0.0",
    .target_id      = CANOPUS_TARGET_ID,
    .prepare        = vibe_prepare,
    .activate       = vibe_activate,
    .deactivate     = vibe_deactivate,
    .stop           = vibe_stop,
    .query          = vibe_query,
    .publish_native_app = 0,
    .publish_native_app_stage = vibe_publish_native_app_stage,
};

CANOPUS_MODULE_ENTRY(s_descriptor)
