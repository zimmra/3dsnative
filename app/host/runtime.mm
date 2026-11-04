#import <Foundation/Foundation.h>

#include "runtime.h"
#include <array>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <utility>
#include "common/file_util.h"
#include "common/logging/backend.h"
#include "common/logging/log.h"
#include "common/settings.h"
#include "core/core.h"
#include "core/perf_stats.h"
#include "core/hle/service/am/am.h"
#include "cytrus/bridge.h"
#include "input_manager.h"
#include "network/network.h"

namespace {
struct InputState { uint32_t buttons{}; int32_t lx{}, ly{}, rx{}, ry{}; };
struct Commands {
    std::array<InputState, N3DS_INPUT_SOURCE_COUNT> sources{};
    bool active{}, stop{}, paused{}, input_dirty{true}, touch_dirty{}, resize_dirty{};
    bool touched{};
    float x{}, y{};
    uint32_t top_w{}, top_h{}, bottom_w{}, bottom_h{}, resolution{};
    n3ds_perf perf{};
} commands;
std::mutex mutex;
std::condition_variable wake;

constexpr std::array<std::pair<uint32_t, int>, 14> buttons{{
    {N3DS_A, InputManager::N3DS_BUTTON_A}, {N3DS_B, InputManager::N3DS_BUTTON_B},
    {N3DS_X, InputManager::N3DS_BUTTON_X}, {N3DS_Y, InputManager::N3DS_BUTTON_Y},
    {N3DS_L, InputManager::N3DS_TRIGGER_L}, {N3DS_R, InputManager::N3DS_TRIGGER_R},
    {N3DS_ZL, InputManager::N3DS_BUTTON_ZL}, {N3DS_ZR, InputManager::N3DS_BUTTON_ZR},
    {N3DS_START, InputManager::N3DS_BUTTON_START}, {N3DS_SELECT, InputManager::N3DS_BUTTON_SELECT},
    {N3DS_LEFT, InputManager::N3DS_DPAD_LEFT}, {N3DS_RIGHT, InputManager::N3DS_DPAD_RIGHT},
    {N3DS_UP, InputManager::N3DS_DPAD_UP}, {N3DS_DOWN, InputManager::N3DS_DPAD_DOWN},
}};

InputState merge(const std::array<InputState, N3DS_INPUT_SOURCE_COUNT>& sources) {
    InputState result;
    int64_t left = -1, right = -1;
    for (const auto& source : sources) {
        result.buttons |= source.buttons;
        int64_t magnitude = int64_t(source.lx) * source.lx + int64_t(source.ly) * source.ly;
        if (magnitude > left) { result.lx = source.lx; result.ly = source.ly; left = magnitude; }
        magnitude = int64_t(source.rx) * source.rx + int64_t(source.ry) * source.ry;
        if (magnitude > right) { result.rx = source.rx; result.ry = source.ry; right = magnitude; }
    }
    return result;
}

void report(const n3ds_boot_config& config, n3ds_stage stage, const char *message,
            uint64_t done = 0, uint64_t total = 0) {
    if (config.callback) config.callback(config.context, stage, done, total, message);
}

void install_update(const n3ds_boot_config& config) {
    if (!config.update_path || !*config.update_path) return;
    const auto marker = std::filesystem::path(config.documents_path) / "installed-update.sha256";
    std::string previous;
    std::ifstream(marker) >> previous;
    const std::string identity = config.update_sha256 ? config.update_sha256 : "";
    const auto content = Service::AM::GetTitleContentPath(Service::FS::MediaType::SDMC,
                                                         config.title_id | 0x0000000E00000000ULL);
    if (!identity.empty() && identity == previous && FileUtil::Exists(content)) return;
    report(config, N3DS_PREPARING_UPDATE, "Preparing update");
    auto result = Service::AM::InstallCIA(config.update_path, [&](size_t done, size_t total) {
        report(config, N3DS_PREPARING_UPDATE, "Preparing update", done, total);
    });
    if (result != Service::AM::InstallStatus::Success)
        throw std::runtime_error("The bundled update could not be installed (status " +
                                 std::to_string(static_cast<unsigned>(result)) + ")");
    if (!identity.empty()) {
        const auto temporary = marker.string() + ".tmp";
        { std::ofstream output(temporary); output << identity << '\n';
          if (!output) throw std::runtime_error("Could not record the installed update"); }
        std::filesystem::rename(temporary, marker);
    }
}
} // namespace

uint32_t n3ds_runtime_abi(void) { return N3DS_RUNTIME_ABI; }

int n3ds_run(const n3ds_boot_config *config_ptr) {
    if (!config_ptr || !config_ptr->game_path || !config_ptr->documents_path ||
        !config_ptr->top.metal_layer || !config_ptr->bottom.metal_layer ||
        !config_ptr->top.width || !config_ptr->top.height ||
        !config_ptr->bottom.width || !config_ptr->bottom.height)
        return 1;
    const auto& config = *config_ptr;
    {
        std::lock_guard lock(mutex);
        if (commands.active) return 2;
        commands.active = true; commands.stop = false; commands.input_dirty = true;
    }
    auto& system = Core::System::GetInstance();
    bool logging = false;
    int outcome = 0;
    try {
        const auto documents = std::filesystem::path(config.documents_path);
        std::filesystem::create_directories(documents / "3dsnative");
        FileUtil::SetUserPath((documents / "3dsnative").string() + "/");
        FileUtil::UpdateUserPath(FileUtil::UserPath::LogDir, documents.string() + "/");
        cytrus::initialize_logging();
        logging = true;
        LOG_INFO(Core, "3dsnative runtime ABI {}; CPU and CPU shader JIT are disabled", N3DS_RUNTIME_ABI);
        if (!std::filesystem::is_regular_file(config.game_path))
            throw std::runtime_error("The bundled game file is missing");
        Settings::values.use_cpu_jit.SetValue(false);
        Settings::values.use_fastinterp.SetValue(true);
        Settings::values.use_shader_jit.SetValue(false);
        Settings::values.graphics_api.SetValue(Settings::GraphicsAPI::Vulkan);
        Settings::values.cpu_clock_percentage.SetValue(100);
        Settings::values.is_new_3ds.SetValue(config.new_3ds);
        Settings::values.resolution_factor.SetValue(config.resolution_scale ? config.resolution_scale : 1);
        install_update(config);
        report(config, N3DS_LOADING, "Starting game");
        cytrus::set_screens(config.top.metal_layer, config.top.height, config.top.width, false);
        cytrus::set_screens(config.bottom.metal_layer, config.bottom.height, config.bottom.width, true);
        cytrus::insert_disc(config.game_path);
        if (!system.IsPoweredOn()) throw std::runtime_error("The emulator could not load the game");
        report(config, N3DS_RUNNING, "");
        uint32_t held = 0, bottom_w = config.bottom.width, bottom_h = config.bottom.height;
        auto next_stats = std::chrono::steady_clock::now() + std::chrono::seconds(1);
        unsigned samples = 0;
        while (true) {
            Commands pending;
            {
                std::unique_lock lock(mutex);
                wake.wait(lock, [] { return commands.stop || !commands.paused; });
                if (commands.stop) break;
                pending = commands;
                commands.input_dirty = commands.touch_dirty = commands.resize_dirty = false;
                commands.resolution = 0;
            }
            @autoreleasepool {
                if (pending.resize_dirty) {
                    cytrus::resize_screens(pending.top_h, pending.top_w, pending.bottom_h, pending.bottom_w);
                    bottom_w = pending.bottom_w; bottom_h = pending.bottom_h;
                }
                if (pending.resolution) {
                    Settings::values.resolution_factor.SetValue(pending.resolution);
                    system.ApplySettings();
                }
                if (pending.input_dirty) {
                    const auto state = merge(pending.sources);
                    for (auto [mask, code] : buttons) {
                        if ((held & mask) == (state.buttons & mask)) continue;
                        if (state.buttons & mask) cytrus::press_button(code);
                        else cytrus::release_button(code);
                    }
                    held = state.buttons;
                    cytrus::move_thumbstick(InputManager::N3DS_CIRCLEPAD, state.lx / 32767.f, state.ly / 32767.f);
                    cytrus::move_thumbstick(InputManager::N3DS_STICK_C, state.rx / 32767.f, state.ry / 32767.f);
                }
                if (pending.touch_dirty || pending.resize_dirty) {
                    if (pending.touched) cytrus::touch_began(pending.x * bottom_w, pending.y * bottom_h);
                    else cytrus::touch_ended();
                }
                auto status = system.RunLoop();
                if (status == Core::System::ResultStatus::ShutdownRequested) break;
                if (status != Core::System::ResultStatus::Success)
                    throw std::runtime_error("Guest execution stopped (status " +
                                             std::to_string(static_cast<unsigned>(status)) + ")");
                if (std::chrono::steady_clock::now() >= next_stats) {
                    auto stats = system.GetAndResetPerfStats();
                    if (++samples % 10 == 0)
                        LOG_INFO(Core, "Performance: game {:.1f} FPS, system {:.1f} FPS, speed {:.0f}%",
                                 stats.game_fps, stats.system_fps, stats.emulation_speed * 100);
                    std::lock_guard lock(mutex);
                    commands.perf = {stats.game_fps, stats.system_fps, stats.emulation_speed};
                    next_stats = std::chrono::steady_clock::now() + std::chrono::seconds(1);
                }
            }
        }
    } catch (const std::exception& error) {
        outcome = 3;
        if (logging) LOG_CRITICAL(Core, "[RED] {}", error.what());
        report(config, N3DS_FAILED, error.what());
    }
    if (system.IsPoweredOn()) system.Shutdown();
    if (InputManager::ButtonHandler()) InputManager::Shutdown();
    if (!Network::GetRoomMember().expired()) Network::Shutdown();
    if (logging) Common::Log::Stop();
    { std::lock_guard lock(mutex); commands.active = false; }
    if (!outcome) report(config, N3DS_STOPPED, "");
    return outcome;
}

void n3ds_pause(bool paused) {
    { std::lock_guard lock(mutex); commands.paused = paused; }
    wake.notify_all();
}
void n3ds_stop(void) {
    { std::lock_guard lock(mutex); commands.stop = true; }
    wake.notify_all();
}
void n3ds_input(unsigned source, uint32_t mask, int32_t lx, int32_t ly, int32_t rx, int32_t ry) {
    for (int32_t axis : {lx, ly, rx, ry})
        if (axis < -32767 || axis > 32767) return;
    std::lock_guard lock(mutex);
    if (source >= commands.sources.size()) return;
    commands.sources[source] = {mask, lx, ly, rx, ry};
    commands.input_dirty = true;
}
void n3ds_touch(bool pressed, float x, float y) {
    if (pressed && (!std::isfinite(x) || !std::isfinite(y) || x < 0 || y < 0 || x >= 1 || y >= 1)) return;
    std::lock_guard lock(mutex);
    commands.touched = pressed; commands.x = x; commands.y = y; commands.touch_dirty = true;
}
void n3ds_resize(uint32_t tw, uint32_t th, uint32_t bw, uint32_t bh) {
    if (!tw || !th || !bw || !bh) return;
    std::lock_guard lock(mutex);
    commands.top_w = tw; commands.top_h = th; commands.bottom_w = bw; commands.bottom_h = bh;
    commands.resize_dirty = true;
}
void n3ds_resolution(uint32_t scale) {
    if (scale < 1 || scale > 10) return;
    std::lock_guard lock(mutex); commands.resolution = scale;
}
n3ds_perf n3ds_get_perf(void) {
    std::lock_guard lock(mutex); return commands.perf;
}
