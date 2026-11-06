// Public C boundary between the locally compiled emulator and the UIKit shell.
// No game data or SDK-specific UIKit/Metal types cross this boundary.
#pragma once
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define N3DS_RUNTIME_ABI 1

enum n3ds_input_source {
    N3DS_INPUT_TOUCH = 0, N3DS_INPUT_CONTROLLER_BASE = 1,
    N3DS_INPUT_KEYBOARD = 9, N3DS_INPUT_AUTOMATION = 10, N3DS_INPUT_SOURCE_COUNT = 11,
};

enum n3ds_button {
    N3DS_A = 1u << 0, N3DS_B = 1u << 1, N3DS_X = 1u << 2, N3DS_Y = 1u << 3,
    N3DS_L = 1u << 6, N3DS_R = 1u << 7, N3DS_ZL = 1u << 8, N3DS_ZR = 1u << 9,
    N3DS_START = 1u << 10, N3DS_SELECT = 1u << 11,
    N3DS_LEFT = 1u << 12, N3DS_UP = 1u << 13, N3DS_RIGHT = 1u << 14, N3DS_DOWN = 1u << 15,
};

typedef struct {
    void *metal_layer; // borrowed CAMetalLayer; the shell retains it while running
    uint32_t width, height; // drawable pixels, not UIKit points
} n3ds_surface;

enum n3ds_stage { N3DS_PREPARING_UPDATE, N3DS_LOADING, N3DS_RUNNING, N3DS_STOPPED, N3DS_FAILED };
// Called on the emulation thread. Copy message before retaining/dispatching it.
typedef void (*n3ds_status_callback)(void *context, enum n3ds_stage stage,
                                    uint64_t done, uint64_t total, const char *message);

typedef struct {
    const char *game_path;
    const char *update_path; // NULL when no update was packed
    const char *update_sha256;
    const char *documents_path;
    uint64_t title_id;
    uint32_t resolution_scale;
    bool new_3ds;
    n3ds_surface top, bottom;
    n3ds_status_callback callback;
    void *context;
} n3ds_boot_config;

typedef struct { double game_fps, system_fps, emulation_speed; } n3ds_perf;

uint32_t n3ds_runtime_abi(void);
// Blocking: call on the shell's boot thread. Returns 0 on a requested stop.
int n3ds_run(const n3ds_boot_config *config);
void n3ds_pause(bool paused);
void n3ds_stop(void);
// Source 0 is touch; sources 1..8 are independent physical controllers.
// Keyboard and Simulator automation have separate sources as well.
// Releasing one source must preserve the others. Axes are in [-32767,32767].
void n3ds_input(unsigned source, uint32_t buttons, int32_t lx, int32_t ly, int32_t rx, int32_t ry);
// Single resistive touch point, normalized on the bottom screen only.
void n3ds_touch(bool pressed, float x, float y);
void n3ds_resize(uint32_t top_width, uint32_t top_height, uint32_t bottom_width, uint32_t bottom_height);
void n3ds_resolution(uint32_t scale);
// 100 = normal speed, 200 = fast-forward. Applied on the emulation thread.
void n3ds_set_speed(uint32_t percent);
uint32_t n3ds_get_speed(void);
n3ds_perf n3ds_get_perf(void);

#ifdef __cplusplus
}
#endif
