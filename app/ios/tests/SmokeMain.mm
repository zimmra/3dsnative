// Simulator integration host for the original ARM11 fixture, not the game frontend.
#import <UIKit/UIKit.h>

#include <chrono>
#include <cmath>
#include <vector>
#include "audio_core/time_stretch.h"
#include "audio_core/audio_types.h"
#include "audio_core/sink_details.h"
#include "common/file_util.h"
#include "common/logging/backend.h"
#include "common/settings.h"
#include "core/core.h"
#include "core/frontend/applets/default_applets.h"
#include "core/frontend/emu_window.h"
#include "core/hle/kernel/kernel.h"
#include "configuration.h"
#include "input_manager.h"
#include "network/network.h"
#include "../../host/runtime.h"

class SmokeWindow final : public Frontend::EmuWindow {
public:
    SmokeWindow() {
        UpdateCurrentFramebufferLayout(400, 480, true);
    }
    void PollEvents() override {}
};

static bool RunAudioProbe(NSString *documents) {
    AudioCore::TimeStretcher stretcher;
    stretcher.SetOutputSampleRate(AudioCore::native_sample_rate);
    constexpr unsigned block = 256;
    std::vector<s16> input(block * 2), output(block * 2), captured;
    for (unsigned start = 0; start < AudioCore::native_sample_rate * 2; start += block) {
        for (unsigned i = 0; i < block; ++i) {
            double time = double(start+i) / AudioCore::native_sample_rate;
            input[2*i] = std::lround(10000 * std::sin(2*M_PI*440*time));
            input[2*i+1] = std::lround(6000 * std::sin(2*M_PI*880*time));
        }
        auto count = stretcher.Process(input.data(), block, output.data(), block);
        captured.insert(captured.end(), output.begin(), output.begin()+2*count);
    }
    double rms[2]{}, frequency[2]{};
    auto frames = captured.size()/2;
    for (unsigned channel = 0; channel < 2; ++channel) {
        unsigned crossings = 0;
        for (size_t i = 1025; i < frames; ++i) {
            double value = captured[2*i+channel]; rms[channel] += value * value;
            if (captured[2*(i-1)+channel] <= 0 && value > 0) ++crossings;
        }
        if (frames > 1025) {
            rms[channel] = std::sqrt(rms[channel] / (frames-1025));
            frequency[channel] = double(crossings)*AudioCore::native_sample_rate / (frames-1025);
        }
    }
    bool passed = frames > 30000 && std::abs(frequency[0]-440) < 5 && std::abs(frequency[1]-880) < 5 &&
                  rms[0] > 6000 && rms[0] < 8000 && rms[1] > 3500 && rms[1] < 5000;
    NSDictionary *result = @{@"passed":@(passed), @"frames":@(frames),
        @"left_hz":@(frequency[0]), @"right_hz":@(frequency[1]), @"left_rms":@(rms[0]), @"right_rms":@(rms[1])};
    [[NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingPrettyPrinted error:nil]
        writeToFile:[documents stringByAppendingPathComponent:@"audio-probe.json"] atomically:YES];
    return passed;
}

static bool RunSmoke(NSString *documents) {
    if (n3ds_runtime_abi() != N3DS_RUNTIME_ABI)
        return false;
    NSString *user = [[documents stringByAppendingPathComponent:@"Cytrus"] stringByAppendingString:@"/"];
    FileUtil::SetUserPath(user.UTF8String);
    FileUtil::UpdateUserPath(FileUtil::UserPath::LogDir,
                            [[documents stringByAppendingString:@"/"] UTF8String]);
    Settings::values.log_filter.SetValue("*:Info Debug.Emulated:Debug");
    Common::Log::Initialize("log.txt");
    Common::Log::SetColorConsoleBackendEnabled(false);
    Common::Log::Start();
    Config{};
    Settings::values.log_filter.SetValue("*:Info Debug.Emulated:Debug");
    Common::Log::Filter filter;
    filter.ParseFilterString(Settings::values.log_filter.GetValue());
    Common::Log::SetGlobalFilter(filter);
    bool audioPassed = RunAudioProbe(documents);
    Settings::values.use_cpu_jit.SetValue(false);
    Settings::values.use_fastinterp.SetValue(true);
    Settings::values.use_shader_jit.SetValue(false);
    Settings::values.graphics_api.SetValue(Settings::GraphicsAPI::Software);
    Settings::values.output_type.SetValue(AudioCore::SinkType::Null);
    Settings::values.cpu_clock_percentage.SetValue(100);
    Settings::values.is_new_3ds.SetValue(false);
    InputManager::Init();
    bool networkInitialized = Network::Init();

    auto& system = Core::System::GetInstance();
    Frontend::RegisterDefaultApplets(system);
    SmokeWindow window;
    NSString *fixture = [NSBundle.mainBundle pathForResource:@"smoke" ofType:@"3dsx"];
    Core::System::ResultStatus status = Core::System::ResultStatus::ErrorGetLoader;
    bool loaded = false;
    if (fixture) {
        status = system.Load(window, fixture.UTF8String);
        loaded = status == Core::System::ResultStatus::Success;
        if (loaded) {
            auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
            for (int i = 0; i < 256 && std::chrono::steady_clock::now() < deadline; ++i) {
                status = system.SingleStep();
                if (status != Core::System::ResultStatus::Success)
                    break;
                if (system.Kernel().GetProcessList().empty())
                    break;
            }
        }
    }
    if (system.IsPoweredOn())
        system.Shutdown();
    if (networkInitialized)
        Network::Shutdown();
    InputManager::Shutdown();
    Common::Log::Stop();

    NSString *log = [NSString stringWithContentsOfFile:[documents stringByAppendingPathComponent:@"log.txt"]
                                             encoding:NSUTF8StringEncoding error:nil];
    bool marker = [log containsString:@"[3dsnative smoke] guest ARM11 executed"];
    NSRegularExpression *exitPattern = [NSRegularExpression regularExpressionWithPattern:@"Process [0-9]+ exiting"
                                                                                options:0 error:nil];
    bool exited = log && [exitPattern numberOfMatchesInString:log options:0 range:NSMakeRange(0, log.length)] == 1;
    bool normalStatus = status == Core::System::ResultStatus::Success ||
                        status == Core::System::ResultStatus::ShutdownRequested;
    bool passed = loaded && marker && exited && normalStatus && audioPassed;
    NSDictionary *result = @{@"state": @"completed", @"passed": @(passed), @"loaded": @(loaded),
                              @"core_status": @(static_cast<unsigned>(status)),
                              @"debug_svc": @(marker), @"exit_svc": @(exited),
                              @"audio_probe": @(audioPassed),
                              @"cpu_jit": @NO, @"shader_jit": @NO};
    NSData *json = [NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingPrettyPrinted error:nil];
    [json writeToFile:[documents stringByAppendingPathComponent:@"smoke-result.json"] atomically:YES];
    return passed;
}

@interface SmokeDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@property(nonatomic, strong) UILabel *statusLabel;
@end

@implementation SmokeDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    UIViewController *controller = [UIViewController new];
    controller.view.backgroundColor = UIColor.systemBackgroundColor;
    self.statusLabel = [UILabel new];
    self.statusLabel.numberOfLines = 0;
    self.statusLabel.textAlignment = NSTextAlignmentCenter;
    self.statusLabel.textColor = UIColor.labelColor;
    self.statusLabel.text = @"Testing ARM11 interpreter…";
    self.statusLabel.translatesAutoresizingMaskIntoConstraints = NO;
    [controller.view addSubview:self.statusLabel];
    [NSLayoutConstraint activateConstraints:@[
        [self.statusLabel.centerXAnchor constraintEqualToAnchor:controller.view.centerXAnchor],
        [self.statusLabel.centerYAnchor constraintEqualToAnchor:controller.view.centerYAnchor],
        [self.statusLabel.widthAnchor constraintEqualToAnchor:controller.view.widthAnchor multiplier:0.9]
    ]];
    self.window.rootViewController = controller;
    [self.window makeKeyAndVisible];
    NSString *documents = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
    NSData *pending = [NSJSONSerialization dataWithJSONObject:@{@"state": @"running", @"passed": @NO}
                                                      options:0 error:nil];
    [pending writeToFile:[documents stringByAppendingPathComponent:@"smoke-result.json"] atomically:YES];
    NSThread *thread = [[NSThread alloc] initWithBlock:^{
        @autoreleasepool {
            bool passed = RunSmoke(documents);
            dispatch_async(dispatch_get_main_queue(), ^{
                self.statusLabel.text = passed ? @"PASS\nARM11 guest executed without JIT" : @"FAIL\nSee Documents/log.txt";
                self.statusLabel.textColor = passed ? UIColor.systemGreenColor : UIColor.systemRedColor;
            });
        }
    }];
    thread.name = @"3dsnative smoke";
    thread.stackSize = 4 * 1024 * 1024;
    [thread start];
    return YES;
}
@end

int main(int argc, char **argv) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass(SmokeDelegate.class));
    }
}
