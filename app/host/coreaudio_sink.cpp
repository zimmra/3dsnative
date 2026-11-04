// iOS RemoteIO output for the Cytrus Sink interface. Based on its CoreAudio sink
// (Jarrod Norwell, 2025); initialize the callback before starting the audio unit.
#include <cstring>
#include <algorithm>
#include <stdexcept>
#include "audio_core/audio_types.h"
#include "audio_core/coreaudio_sink.h"
#include "common/logging/log.h"

namespace AudioCore {
namespace {
void checked(OSStatus status, const char* operation) {
    if (status != noErr)
        throw std::runtime_error(std::string(operation) + " failed (OSStatus " + std::to_string(status) + ")");
}
}
CoreAudioSink::CoreAudioSink(std::string) : audio_unit(nullptr) {
    AudioComponentDescription description{};
    description.componentType = kAudioUnitType_Output;
    description.componentSubType = kAudioUnitSubType_RemoteIO;
    description.componentManufacturer = kAudioUnitManufacturer_Apple;
    auto component = AudioComponentFindNext(nullptr, &description);
    if (!component) throw std::runtime_error("RemoteIO output component unavailable");
    try {
        checked(AudioComponentInstanceNew(component, &audio_unit), "AudioComponentInstanceNew");
        AudioStreamBasicDescription format{};
        FillOutASBDForLPCM(format, native_sample_rate, 2, 16, 16, false, false);
        checked(AudioUnitSetProperty(audio_unit, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input,
                                     0, &format, sizeof(format)), "RemoteIO stream format");
        AURenderCallbackStruct callback{NativeCallback, this};
        checked(AudioUnitSetProperty(audio_unit, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input,
                                     0, &callback, sizeof(callback)), "RemoteIO callback");
        checked(AudioUnitInitialize(audio_unit), "AudioUnitInitialize");
    } catch (...) {
        if (audio_unit) AudioComponentInstanceDispose(audio_unit);
        audio_unit = nullptr;
        throw;
    }
}
CoreAudioSink::~CoreAudioSink() {
    if (!audio_unit) return;
    AudioOutputUnitStop(audio_unit);
    AudioUnitUninitialize(audio_unit);
    AudioComponentInstanceDispose(audio_unit);
}
void CoreAudioSink::SetCallback(std::function<void(s16*, std::size_t)> callback) {
    // Sink construction installs this once, before RemoteIO's render thread runs.
    cb = std::move(callback);
    checked(AudioOutputUnitStart(audio_unit), "AudioOutputUnitStart");
    LOG_INFO(Audio_Sink, "RemoteIO started: stereo signed 16-bit, {} Hz", native_sample_rate);
}
OSStatus CoreAudioSink::NativeCallback(void* context, AudioUnitRenderActionFlags*, const AudioTimeStamp*,
                                      UInt32, UInt32 frames, AudioBufferList* buffers) {
    auto* sink = static_cast<CoreAudioSink*>(context);
    for (UInt32 i = 0; i < buffers->mNumberBuffers; ++i) {
        auto& buffer = buffers->mBuffers[i];
        if (!buffer.mData) continue;
        if (sink->cb) sink->cb(static_cast<s16*>(buffer.mData), std::min<std::size_t>(frames, buffer.mDataByteSize / 4));
        else std::memset(buffer.mData, 0, buffer.mDataByteSize);
    }
    return noErr;
}
unsigned int CoreAudioSink::GetNativeSampleRate() const { return native_sample_rate; }
std::vector<std::string> ListCoreAudioSinkDevices() { return {"auto"}; }
}
