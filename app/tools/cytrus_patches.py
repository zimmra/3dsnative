"""Reviewed source adaptations for the pinned public Cytrus checkout."""
from pathlib import Path


def fast_forward_presentation(source: Path, target: Path) -> None:
    relative = "video_core/renderer_vulkan/renderer_vulkan.cpp"
    original = (source / relative).read_text()
    marker = "void RendererVulkan::SwapBuffers() {\n    system.perf_stats->StartSwap();"
    if original.count(marker) != 1:
        raise RuntimeError("Review upstream Vulkan presentation before applying fast-forward")
    patched = original.replace(marker, marker + """
    // At 2x, compose the host displays once per two guest VBlanks. FIFO
    // presentation can otherwise block the guest on each display refresh.
    // Continue submitting guest GPU work and running all guest frame timing.
    if (Settings::GetFrameLimit() == 200 && (current_frame & 1) &&
        !settings.screenshot_requested.load()) {
        scheduler.Flush();
        system.perf_stats->EndSwap();
        rasterizer.TickFrame();
        EndFrame();
        return;
    }
""")
    output = target / relative
    if output.read_text() != patched:
        output.write_text(patched)


def audio_fixes(source: Path, target: Path) -> None:
    edits = {
        "audio_core/time_stretch.cpp": [
            ("namespace AudioCore {", "namespace AudioCore {\nstatic_assert(std::is_same_v<soundtouch::SAMPLETYPE, s16>, \"Pinned SoundTouch binary uses integer PCM\");"),
        ],
        "audio_core/dsp_interface.cpp": [
            ("    sink->SetCallback(\n", "    time_stretcher.SetOutputSampleRate(sink->GetNativeSampleRate());\n    sink->SetCallback(\n"),
            (" });\n    time_stretcher.SetOutputSampleRate(sink->GetNativeSampleRate());", " });"),
            ("perf_stats.emulation_speed <= 95;", "(perf_stats.emulation_speed <= 0.95 || Settings::GetFrameLimit() > 100);"),
            ("fifo.Pop(buffer, num_frames - frames_written)", "fifo.Pop(buffer + 2 * frames_written, num_frames - frames_written)"),
        ],
    }
    for relative, replacements in edits.items():
        content = (source / relative).read_text()
        for before, after in replacements:
            if content.count(before) != 1:
                raise RuntimeError(f"Review upstream audio adaptation: {relative}: {before}")
            content = content.replace(before, after)
        output = target / relative
        if output.read_text() != content:
            output.write_text(content)


def simulator_texel_buffers(source: Path, target: Path) -> None:
    """Simulator Metal only permits linear textures in private GPU buffers.

    Keep mapped uploads in the existing ring and copy committed texel ranges
    into a private ring before shaders read them. Device builds keep the
    upstream shared-memory path. No guest rendering operation is skipped.
    """
    edits = {
        "include/video_core/renderer_vulkan/vk_stream_buffer.h": [
            ("return buffer;", "return device_buffer ? device_buffer : buffer;"),
            ("vk::DeviceMemory memory;", "vk::Buffer device_buffer{};\n    vk::DeviceMemory device_memory{};\n    vk::DeviceMemory memory;"),
        ],
        "video_core/renderer_vulkan/vk_stream_buffer.cpp": [
            ('#include <algorithm>', '#include <algorithm>\n#include <TargetConditionals.h>'),
            ('    CreateBuffers(size);', '''#if TARGET_OS_SIMULATOR
    if (usage & vk::BufferUsageFlagBits::eUniformTexelBuffer)
        usage |= vk::BufferUsageFlagBits::eTransferSrc;
#endif
    CreateBuffers(size);'''),
            ('    device.unmapMemory(memory);', '''    if (device_buffer) device.destroyBuffer(device_buffer);
    if (device_memory) device.freeMemory(device_memory);
    device.unmapMemory(memory);'''),
            ('    offset += size;', '''    if (device_buffer && size) {
        scheduler.Record([src = buffer, dst = device_buffer, start = offset, size](vk::CommandBuffer command) {
            const vk::BufferMemoryBarrier before{
                .srcAccessMask = vk::AccessFlagBits::eShaderRead,
                .dstAccessMask = vk::AccessFlagBits::eTransferWrite,
                .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .buffer = dst, .offset = start, .size = size,
            };
            command.pipelineBarrier(vk::PipelineStageFlagBits::eAllCommands,
                                    vk::PipelineStageFlagBits::eTransfer, {}, {}, before, {});
            command.copyBuffer(src, dst, vk::BufferCopy{start, start, size});
            const vk::BufferMemoryBarrier after{
                .srcAccessMask = vk::AccessFlagBits::eTransferWrite,
                .dstAccessMask = vk::AccessFlagBits::eShaderRead,
                .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .buffer = dst, .offset = start, .size = size,
            };
            command.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,
                                    vk::PipelineStageFlagBits::eAllCommands, {}, {}, after, {});
        });
    }
    offset += size;'''),
            ('            stream_buffer_size = static_cast<u64>(attempt_size);', '''#if TARGET_OS_SIMULATOR
            if (usage & vk::BufferUsageFlagBits::eUniformTexelBuffer) {
                device_buffer = device.createBuffer({
                    .size = attempt_size,
                    .usage = vk::BufferUsageFlagBits::eUniformTexelBuffer | vk::BufferUsageFlagBits::eTransferDst,
                });
                const auto required = device.getBufferMemoryRequirements(device_buffer);
                auto private_type = FindMemoryType(memory_properties, vk::MemoryPropertyFlagBits::eDeviceLocal,
                    required.memoryTypeBits, vk::MemoryPropertyFlagBits::eHostVisible);
                if (!private_type) throw std::runtime_error("Simulator has no private texel buffer memory type");
                device_memory = device.allocateMemory({.allocationSize = required.size, .memoryTypeIndex = *private_type});
                device.bindBufferMemory(device_buffer, device_memory, 0);
            }
#endif
            stream_buffer_size = static_cast<u64>(attempt_size);'''),
            ('            // Allocation failed, clean up and retry smaller', '''            // Allocation failed, clean up and retry smaller
            if (device_buffer) { device.destroyBuffer(device_buffer); device_buffer = VK_NULL_HANDLE; }
            if (device_memory) { device.freeMemory(device_memory); device_memory = VK_NULL_HANDLE; }
            if (memory) { if (mapped) device.unmapMemory(memory); device.freeMemory(memory); memory = VK_NULL_HANDLE; mapped = nullptr; }'''),
        ],
        "video_core/renderer_vulkan/vk_rasterizer.cpp": [
            ('    texture_lf_buffer.Commit(', '    renderpass_cache.EndRendering();\n    texture_lf_buffer.Commit('),
            ('    texture_buffer.Commit(', '    renderpass_cache.EndRendering();\n    texture_buffer.Commit('),
        ],
    }
    for relative, replacements in edits.items():
        text = (source / relative).read_text()
        for before, after in replacements:
            if text.count(before) != 1:
                raise RuntimeError(f"Review upstream Simulator buffer adaptation: {relative}: {before}")
            text = text.replace(before, after)
        output = target / relative
        if output.read_text() != text:
            output.write_text(text)
