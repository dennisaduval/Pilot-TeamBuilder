// All observations are retained. Coordinates are upright, top-left pixel units.
import Foundation
import Vision
import ImageIO
import CoreML

struct Face: Encodable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let confidence: Float
}

struct Result: Encodable {
    let schema_version = 1
    let width: Int
    let height: Int
    let faces: [Face]
}

do {
    let args = Array(CommandLine.arguments.dropFirst())
    guard args.count == 1 || (args.count == 2 && args[1] == "--cpu") else {
        throw NSError(domain: "TeamFaceDetector", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Usage: TeamFaceDetector upright.png [--cpu]"])
    }
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: args[0]) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw NSError(domain: "TeamFaceDetector", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Cannot decode input image."])
    }
    let request = VNDetectFaceRectanglesRequest()
    request.revision = VNDetectFaceRectanglesRequestRevision3
    if args.contains("--cpu") {
        if #available(macOS 14.0, *) {
            for (stage, devices) in try request.supportedComputeStageDevices {
                guard let cpu = devices.first(where: { if case .cpu = $0 { return true }; return false }) else {
                    throw NSError(domain: "TeamFaceDetector", code: 3,
                                  userInfo: [NSLocalizedDescriptionKey: "CPU-only execution is unavailable."])
                }
                request.setComputeDevice(cpu, for: stage)
            }
        } else {
            request.usesCPUOnly = true
        }
    }
    let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
    try handler.perform([request])
    let faces = (request.results ?? []).map { observation -> Face in
        let box = observation.boundingBox
        return Face(x: Double(box.minX) * Double(image.width),
                    y: (1 - Double(box.maxY)) * Double(image.height),
                    width: Double(box.width) * Double(image.width),
                    height: Double(box.height) * Double(image.height),
                    confidence: observation.confidence)
    }
    let data = try JSONEncoder().encode(Result(width: image.width, height: image.height, faces: faces))
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
