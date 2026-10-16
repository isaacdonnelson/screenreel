import AVFoundation
import CoreGraphics
import CoreImage
import CoreMedia
import CoreVideo
import Foundation

func beautify(takePath: String, preview: Bool) async -> [String: Any] {
    do {
        return try await renderTake(absolutePath(takePath), preview: preview)
    } catch {
        return ["ok": false, "error": error.localizedDescription]
    }
}

private func renderTake(_ take: URL, preview: Bool) async throws -> [String: Any] {
    let editURL = take.appendingPathComponent("edit.json")
    guard FileManager.default.fileExists(atPath: editURL.path) else {
        throw ToolError("Missing edit.json. Run screenreel record stop first.")
    }
    var edit = try loadEdit(at: editURL)
    if preview {
        edit.canvas.width = 960
        edit.canvas.height = 540
    }
    let rawURL = resolveTakeFile(edit.source, take: take)
    let eventsURL = resolveTakeFile(edit.events, take: take)
    guard FileManager.default.fileExists(atPath: rawURL.path) else {
        throw ToolError("Missing source video at \(rawURL.path)")
    }
    let events = FileManager.default.fileExists(atPath: eventsURL.path) ? try loadEvents(at: eventsURL) : []
    let zooms = resolvedZooms(edit: edit, events: events)

    let asset = AVURLAsset(url: rawURL)
    let duration = try await asset.load(.duration).seconds
    guard duration.isFinite, duration > 0 else {
        throw ToolError("The recording has no video duration")
    }
    guard let track = try await asset.loadTracks(withMediaType: .video).first else {
        throw ToolError("The recording has no video track")
    }
    let reader = try AVAssetReader(asset: asset)
    let output = AVAssetReaderTrackOutput(track: track, outputSettings: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
    ])
    output.alwaysCopiesSampleData = true
    guard reader.canAdd(output) else { throw ToolError("Could not read the recording") }
    reader.add(output)
    guard reader.startReading() else {
        throw ToolError(reader.error?.localizedDescription ?? "Could not read the recording")
    }

    let fps = edit.canvas.fps
    let map = buildTimeMap(duration: duration, segments: edit.segments, cuts: edit.cuts)
    let sourceFrameCount = max(1, Int((duration * Double(fps)).rounded(.down)))
    let frameCount = max(1, Int((map.outputDuration * Double(fps)).rounded(.down)))
    var sample = output.copyNextSampleBuffer()
    var current: CMSampleBuffer?
    var sourceSize = edit.sourceSize ?? .zero
    if sourceSize.width < 2, let first = sample, let buffer = CMSampleBufferGetImageBuffer(first) {
        sourceSize = CGSize(width: CVPixelBufferGetWidth(buffer), height: CVPixelBufferGetHeight(buffer))
    }

    let outputURL = take.appendingPathComponent(preview ? "preview.mp4" : "out.mp4")
    try? FileManager.default.removeItem(at: outputURL)
    let writer = try AVAssetWriter(outputURL: outputURL, fileType: .mp4)
    let settings: [String: Any] = [
        AVVideoCodecKey: AVVideoCodecType.h264,
        AVVideoWidthKey: edit.canvas.width,
        AVVideoHeightKey: edit.canvas.height,
        AVVideoCompressionPropertiesKey: [
            AVVideoAverageBitRateKey: 12_000_000,
            AVVideoExpectedSourceFrameRateKey: fps,
            AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
            AVVideoMaxKeyFrameIntervalKey: fps * 2,
        ],
    ]
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
    input.expectsMediaDataInRealTime = false
    let adaptor = AVAssetWriterInputPixelBufferAdaptor(
        assetWriterInput: input,
        sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: edit.canvas.width,
            kCVPixelBufferHeightKey as String: edit.canvas.height,
            kCVPixelBufferCGImageCompatibilityKey as String: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey as String: true,
        ]
    )
    guard writer.canAdd(input) else { throw ToolError("Could not create the output video") }
    writer.add(input)
    guard writer.startWriting() else {
        throw ToolError(writer.error?.localizedDescription ?? "Could not create the output video")
    }
    writer.startSession(atSourceTime: .zero)

    let clicks = events.filter { $0.type == "click" || $0.type == "down" }
    let ciContext = CIContext(options: [.cacheIntermediates: false])
    let fallback = CGPoint(x: sourceSize.width / 2, y: sourceSize.height / 2)
    let cursors = cursorTrack(events: scaledEvents(events, from: edit.sourceSize, to: sourceSize), frameCount: sourceFrameCount, fps: Double(fps), fallback: fallback)
    let scaledClicks = scaledEvents(clicks, from: edit.sourceSize, to: sourceSize)
    let scaledZooms = scaleZooms(zooms, from: edit.sourceSize, to: sourceSize)
    let scaledBlurs = scaleBlurs(edit.blurs, from: edit.sourceSize, to: sourceSize)

    var written = 0
    for index in 0..<frameCount {
        let rendered: CGImage? = try autoreleasepool { () throws -> CGImage? in
            let outputTime = Double(index) / Double(fps)
            let time = map.sourceTime(at: outputTime)
            while let pending = sample {
                let pts = CMSampleBufferGetPresentationTimeStamp(pending).seconds
                if pts <= time + 0.5 / Double(fps) {
                    current = pending
                    sample = output.copyNextSampleBuffer()
                } else {
                    break
                }
            }
            guard let current, let buffer = CMSampleBufferGetImageBuffer(current) else { return nil }
            if sourceSize.width < 2 {
                sourceSize = CGSize(width: CVPixelBufferGetWidth(buffer), height: CVPixelBufferGetHeight(buffer))
            }
            let image = try cgImage(from: buffer)
            let cursorIndex = min(cursors.count - 1, max(0, Int((time * Double(fps)).rounded())))
            let cursor = cursors.isEmpty ? fallback : cursors[cursorIndex]
            let shot = camera(at: time, zooms: scaledZooms, style: edit.style, cursor: cursor)
            let ripples = scaledClicks.compactMap { click -> Ripple? in
                let age = time - click.t
                guard age >= 0, age <= 0.42 else { return nil }
                return Ripple(x: click.x, y: click.y, age: age)
            }
            let blursNow = scaledBlurs.filter { blur in
                time >= (blur.t0 ?? 0) && time < (blur.t1 ?? Double.greatestFiniteMagnitude)
            }
            let coversNow = scaleCovers(edit.covers, from: edit.sourceSize, to: sourceSize).filter { cover in
                time >= (cover.t0 ?? 0) && time < (cover.t1 ?? Double.greatestFiniteMagnitude)
            }
            return try renderFrame(
                source: image,
                canvas: edit.canvas,
                style: edit.style,
                cursor: cursors.isEmpty ? nil : cursor,
                focus: shot.focus,
                zoomScale: shot.scale,
                ripples: ripples,
                blurs: blursNow,
                covers: coversNow,
                context: ciContext
            )
        }
        guard let rendered else { continue }
        guard let pool = adaptor.pixelBufferPool else {
            throw ToolError("Could not allocate an output frame")
        }
        var pixelBuffer: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pixelBuffer) == kCVReturnSuccess, let pixelBuffer else {
            throw ToolError("Could not allocate an output frame")
        }
        copyImage(rendered, into: pixelBuffer)
        while !input.isReadyForMoreMediaData {
            try await Task.sleep(nanoseconds: 2_000_000)
        }
        let presentation = CMTime(value: CMTimeValue(index), timescale: CMTimeScale(fps))
        if !adaptor.append(pixelBuffer, withPresentationTime: presentation) {
            throw ToolError(writer.error?.localizedDescription ?? "Could not write a frame")
        }
        written += 1
        if index % fps == 0 {
            log("frame \(index)/\(frameCount)")
        }
    }
    if written == 0 {
        throw ToolError("The recording produced no frames")
    }

    input.markAsFinished()
    await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
        writer.finishWriting { continuation.resume() }
    }
    guard writer.status == .completed else {
        throw ToolError(writer.error?.localizedDescription ?? "Could not finish the video")
    }
    faststart(outputURL)
    log("wrote \(outputURL.path)")
    let warnings = collectWarnings(events: events, edit: edit, duration: duration, sourceSize: sourceSize)
    return [
        "ok": true,
        "take": take.path,
        "output": outputURL.path,
        "width": edit.canvas.width,
        "height": edit.canvas.height,
        "fps": fps,
        "duration": Double(written) / Double(fps),
        "sourceDuration": duration,
        "frames": written,
        "zoomCount": zooms.count,
        "resolvedZooms": zooms.map { zoomObject($0) },
        "steps": stepTimeline(events: events, duration: duration, map: map),
        "warnings": warnings,
    ]
}

func cgImage(from buffer: CVPixelBuffer) throws -> CGImage {
    CVPixelBufferLockBaseAddress(buffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
    let width = CVPixelBufferGetWidth(buffer)
    let height = CVPixelBufferGetHeight(buffer)
    let stride = CVPixelBufferGetBytesPerRow(buffer)
    guard let base = CVPixelBufferGetBaseAddress(buffer) else {
        throw ToolError("Could not read a source frame")
    }
    let data = Data(bytes: base, count: stride * height)
    guard let provider = CGDataProvider(data: data as CFData) else {
        throw ToolError("Could not read a source frame")
    }
    let info = CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue
    guard let image = CGImage(
        width: width,
        height: height,
        bitsPerComponent: 8,
        bitsPerPixel: 32,
        bytesPerRow: stride,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGBitmapInfo(rawValue: info),
        provider: provider,
        decode: nil,
        shouldInterpolate: true,
        intent: .defaultIntent
    ) else {
        throw ToolError("Could not read a source frame")
    }
    return image
}

private func scaledEvents(_ events: [Event], from recorded: CGSize?, to video: CGSize) -> [Event] {
    let scale = eventScale(recorded: recorded, video: video)
    return events.map { event in
        Event(t: event.t, type: event.type, x: event.x * scale.x, y: event.y * scale.y, button: event.button)
    }
}

private func scaleZooms(_ zooms: [Zoom], from recorded: CGSize?, to video: CGSize) -> [Zoom] {
    let scale = eventScale(recorded: recorded, video: video)
    return zooms.map { Zoom(t: $0.t, x: $0.x * scale.x, y: $0.y * scale.y, scale: $0.scale, hold: $0.hold) }
}

private func scaleCovers(_ covers: [CoverRect], from recorded: CGSize?, to video: CGSize) -> [CoverRect] {
    let scale = eventScale(recorded: recorded, video: video)
    return covers.map { cover in
        CoverRect(
            x: cover.x * scale.x,
            y: cover.y * scale.y,
            width: cover.width * scale.x,
            height: cover.height * scale.y,
            t0: cover.t0,
            t1: cover.t1,
            color: cover.color
        )
    }
}

func faststart(_ url: URL) {
    let candidates = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]
    guard let ffmpeg = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else { return }
    let temporary = url.deletingPathExtension().appendingPathExtension("faststart.mp4")
    try? FileManager.default.removeItem(at: temporary)
    let process = Process()
    process.executableURL = URL(fileURLWithPath: ffmpeg)
    process.arguments = ["-y", "-i", url.path, "-c", "copy", "-movflags", "+faststart", temporary.path]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do {
        try process.run()
        process.waitUntilExit()
    } catch {
        return
    }
    guard process.terminationStatus == 0 else {
        try? FileManager.default.removeItem(at: temporary)
        return
    }
    do {
        try FileManager.default.removeItem(at: url)
        try FileManager.default.moveItem(at: temporary, to: url)
    } catch {
        try? FileManager.default.removeItem(at: temporary)
    }
}

private func scaleBlurs(_ blurs: [BlurRect], from recorded: CGSize?, to video: CGSize) -> [BlurRect] {
    let scale = eventScale(recorded: recorded, video: video)
    return blurs.map { blur in
        BlurRect(
            x: blur.x * scale.x,
            y: blur.y * scale.y,
            width: blur.width * scale.x,
            height: blur.height * scale.y,
            radius: blur.radius,
            t0: blur.t0,
            t1: blur.t1
        )
    }
}

private func eventScale(recorded: CGSize?, video: CGSize) -> CGPoint {
    guard let recorded, recorded.width > 1, recorded.height > 1, video.width > 1, video.height > 1 else {
        return CGPoint(x: 1, y: 1)
    }
    if abs(recorded.width - video.width) < 2 && abs(recorded.height - video.height) < 2 {
        return CGPoint(x: 1, y: 1)
    }
    return CGPoint(x: video.width / recorded.width, y: video.height / recorded.height)
}
