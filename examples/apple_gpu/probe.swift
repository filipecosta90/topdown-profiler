import Foundation
import Metal

let passes = CommandLine.arguments.count > 1 ? (Int(CommandLine.arguments[1]) ?? 4) : 4
precondition((1...500).contains(passes))

guard let device = MTLCreateSystemDefaultDevice(),
      let queue = device.makeCommandQueue() else {
    fatalError("No Metal GPU or command queue")
}

print("device=\(device.name)")
for set in device.counterSets ?? [] {
    print("counter_set=\(set.name) counters=\(set.counters.map(\.name).joined(separator: ","))")
}
for (name, point) in [
    ("stage", MTLCounterSamplingPoint.atStageBoundary),
    ("draw", .atDrawBoundary),
    ("dispatch", .atDispatchBoundary),
    ("blit", .atBlitBoundary),
] {
    print("sample_at_\(name)=\(device.supportsCounterSampling(point))")
}

let source = """
#include <metal_stdlib>
using namespace metal;
kernel void burn(device float *values [[buffer(0)]], uint id [[thread_position_in_grid]]) {
    float x = values[id];
    for (uint i = 0; i < 1024; ++i) {
        x = fma(x, 1.000001f, 0.000003f);
    }
    values[id] = x;
}
"""
let library = try device.makeLibrary(source: source, options: nil)
guard let function = library.makeFunction(name: "burn") else {
    fatalError("Metal kernel missing")
}
let pipeline = try device.makeComputePipelineState(function: function)
let count = 16_384
guard let buffer = device.makeBuffer(length: count * MemoryLayout<Float>.stride,
                                     options: .storageModeShared) else {
    fatalError("Could not allocate GPU buffer")
}
let values = buffer.contents().bindMemory(to: Float.self, capacity: count)
for i in 0..<count { values[i] = Float(i % 97) / 97.0 }

var totalGPUTime = 0.0
for pass in 0..<passes {
    guard let command = queue.makeCommandBuffer(),
          let encoder = command.makeComputeCommandEncoder() else {
        fatalError("Could not encode GPU work")
    }
    command.label = "topdown GPU probe pass \(pass)"
    encoder.label = "burn compute pass \(pass)"
    encoder.setComputePipelineState(pipeline)
    encoder.setBuffer(buffer, offset: 0, index: 0)
    encoder.dispatchThreads(MTLSize(width: count, height: 1, depth: 1),
                            threadsPerThreadgroup: MTLSize(width: 256, height: 1, depth: 1))
    encoder.endEncoding()
    command.commit()
    command.waitUntilCompleted()
    guard command.status == .completed else {
        fatalError("GPU command failed: \(String(describing: command.error))")
    }
    totalGPUTime += command.gpuEndTime - command.gpuStartTime
}

let checksum = (0..<count).reduce(0.0) { $0 + Double(values[$1]) }
print("passes=\(passes) items=\(count) gpu_seconds=\(totalGPUTime) checksum=\(checksum)")
