import AVFoundation
import CoreMedia
import Foundation
import ImageIO
import UniformTypeIdentifiers

func extractFrames(takePath: String, flags: Flags) async -> [String: Any] {
    do {
        return try await writeFrames(take: absolutePath(takePath), flags: flags)
    } catch {
        return ["ok": false, "error": error.localizedDescription]
    }
}

private func writeFrames(take: URL, flags: Flags) async throws -> [String: Any] {
    let video = take.appendingPathComponent("out.mp4")
    guard FileManager.default.fileExists(atPath: video.path) else {
        throw ToolError("Missing out.mp4. Run screenreel beautify first.")
    }
    let asset = AVURLAsset(url: video)
    let duration = try await asset.load(.duration).seconds
    guard duration.isFinite, duration > 0 else {
        throw ToolError("The rendered video has no duration")
    }
    var times = try flags.at.map { try parseTimes($0) } ?? []
    if let everyText = flags.every {
        guard let every = Double(everyText), every > 0 else {
            throw ToolError("--every needs a number of seconds")
        }
        var cursor = 0.0
        while cursor < duration {
            times.append(cursor)
            cursor += every
        }
    }
    if flags.markers {
        let eventsURL = take.appendingPathComponent("events.jsonl")
        let events = FileManager.default.fileExists(atPath: eventsURL.path) ? try loadEvents(at: eventsURL) : []
        let editURL = take.appendingPathComponent("edit.json")
        let edit = FileManager.default.fileExists(atPath: editURL.path) ? try loadEdit(at: editURL) : EditList()
        let raw = take.appendingPathComponent("raw.mov")
        let sourceDuration = FileManager.default.fileExists(atPath: raw.path) ? try await mediaDuration(raw) : duration
        let map = buildTimeMap(duration: sourceDuration, segments: edit.segments, cuts: edit.cuts)
        times.append(contentsOf: events.filter { $0.type == "marker" }.map { map.outputTime(at: $0.t) })
    }
    times = Array(Set(times.map { ($0 * 100).rounded() / 100 })).sorted()
    guard !times.isEmpty else {
        throw ToolError("Pass --at, --every, or --markers")
    }
    for time in times where time < 0 || time > duration + 0.05 {
        throw ToolError(String(format: "Time %.2f is outside the video (%.2f seconds)", time, duration))
    }
    let directory = take.appendingPathComponent("frames")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let generator = AVAssetImageGenerator(asset: asset)
    generator.appliesPreferredTrackTransform = true
    generator.requestedTimeToleranceBefore = CMTime(seconds: 0.05, preferredTimescale: 600)
    generator.requestedTimeToleranceAfter = CMTime(seconds: 0.05, preferredTimescale: 600)

    var paths: [String] = []
    for time in times {
        var actual = CMTime.zero
        let image = try generator.copyCGImage(at: CMTime(seconds: time, preferredTimescale: 600), actualTime: &actual)
        let name = String(format: "t-%.2f.png", time)
        let url = directory.appendingPathComponent(name)
        try writePNG(image, to: url)
        paths.append(url.path)
    }
    return [
        "ok": true,
        "take": take.path,
        "video": video.path,
        "duration": duration,
        "frames": paths,
    ]
}

func writePNG(_ image: CGImage, to url: URL) throws {
    guard let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
        throw ToolError("Could not create \(url.path)")
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else {
        throw ToolError("Could not write \(url.path)")
    }
}

func parseTimes(_ text: String) throws -> [Double] {
    let parts = text.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    if parts.isEmpty { throw ToolError("--at needs one or more times in seconds, like 1.2,4.0") }
    return try parts.map { part in
        guard let value = Double(part) else {
            throw ToolError("Could not read time \"\(part)\"")
        }
        return value
    }
}
