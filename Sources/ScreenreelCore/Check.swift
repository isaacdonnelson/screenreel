import AVFoundation
import CoreGraphics
import CoreImage
import CoreVideo
import Foundation

private var failures = 0

private func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    if condition() { return }
    failures += 1
    FileHandle.standardError.write(Data("FAIL \(message)\n".utf8))
}

private func near(_ value: Double, _ expected: Double, tolerance: Double = 0.001) -> Bool {
    abs(value - expected) <= tolerance
}

private func runChecks() throws {
    let main = CGRect(x: 0, y: 0, width: 1440, height: 900)
    let topLeft = displayTopLeft(displayBounds: main, mainBounds: main)
    check(topLeft == CGPoint(x: 0, y: 0), "main display top-left should be the origin")
    let map = DisplayMap(
        topLeftX: topLeft.x,
        topLeftY: topLeft.y,
        pointWidth: 1440,
        pointHeight: 900,
        pixelWidth: 2880,
        pixelHeight: 1800
    )
    let mapped = map.pixel(eventX: 100, eventY: 50)
    check(near(mapped.0, 200) && near(mapped.1, 100), "retina event should land on pixel \(mapped)")

    let style = Style()
    let zoom = Zoom(t: 1, x: 10, y: 20, scale: style.zoomScale, hold: style.zoomHold)
    check(near(zoomStrength(at: 0.9, zoom: zoom, style: style), 0), "zoom should not start early")
    check(near(zoomStrength(at: 1 + style.zoomIn * 0.5, zoom: zoom, style: style), 0.5), "zoom midpoint should be 0.5")
    check(near(zoomStrength(at: 1 + style.zoomIn + 0.1, zoom: zoom, style: style), 1), "zoom should hold at full strength")
    check(near(zoomStrength(at: 1 + style.zoomIn + style.zoomHold + style.zoomOut, zoom: zoom, style: style), 0), "zoom should end")

    let sped = buildTimeMap(
        duration: 12,
        segments: [Segment(start: 0, end: 10, speed: 2), Segment(start: 10, end: 12, speed: 1)],
        cuts: []
    )
    check(near(sped.outputDuration, 7), "sped output should be 7s, got \(sped.outputDuration)")
    check(near(sped.sourceTime(at: 5), 10), "output 5s should land on source 10s")
    check(near(sped.sourceTime(at: 6), 11), "output 6s should land on source 11s")
    check(near(sped.outputTime(at: 11), 6), "source 11s should land on output 6s")
    let identity = buildTimeMap(duration: 4, segments: [], cuts: [])
    check(near(identity.outputDuration, 4) && near(identity.sourceTime(at: 1.5), 1.5), "empty timing stays 1:1")
    let waited = plannedTiming(
        events: [
            Event(t: 1, type: "down", x: 10, y: 10, button: "left"),
            Event(t: 20, type: "down", x: 10, y: 10, button: "left"),
        ],
        duration: 21,
        steps: []
    )
    check(!waited.cuts.isEmpty, "a long wait should be cut")

    let events = [
        Event(t: 0.1, type: "move", x: 10, y: 20, button: nil),
        Event(t: 1.0, type: "click", x: 100, y: 200, button: "left"),
        Event(t: 1.1, type: "click", x: 110, y: 210, button: "left"),
        Event(t: 2.5, type: "move", x: 50, y: 60, button: nil),
        Event(t: 3.0, type: "key", x: 0, y: 0, button: nil),
        Event(t: 3.2, type: "key", x: 0, y: 0, button: nil),
        Event(t: 3.5, type: "key", x: 0, y: 0, button: nil),
        Event(t: 5.0, type: "key", x: 0, y: 0, button: nil),
    ]
    let zooms = autoZooms(events: events, style: style)
    check(zooms.count == 3, "expected 3 zooms, got \(zooms.count)")
    if zooms.count == 3 {
        check(near(zooms[0].t, 1) && near(zooms[0].x, 100), "first zoom should be the click")
        check(near(zooms[1].t, 3) && near(zooms[1].x, 50) && near(zooms[1].y, 60), "typing burst should use the cursor at \(zooms[1])")
        check(near(zooms[2].t, 5), "a later key should start its own zoom")
    }

    let fitted = frameLayout(
        sourceSize: CGSize(width: 1920, height: 1200),
        canvasSize: CGSize(width: 1920, height: 1080),
        padding: 72,
        focus: CGPoint(x: 960, y: 600),
        zoomScale: 1
    )
    check(near(fitted.crop.width, 1920, tolerance: 0.1) && near(fitted.crop.height, 1200, tolerance: 0.1), "zoom 1 should show the whole recording")
    check(near(fitted.dest.height, 936, tolerance: 0.1) && near(fitted.dest.minY, 72, tolerance: 0.1), "recording should sit inside the padding")

    let cropped = frameLayout(
        sourceSize: CGSize(width: 1920, height: 1200),
        canvasSize: CGSize(width: 1920, height: 1080),
        padding: 72,
        focus: CGPoint(x: 100, y: 100),
        zoomScale: 2
    )
    check(cropped.crop.origin == .zero && near(cropped.crop.width, 960, tolerance: 0.1), "zoom crop should stay inside the source")

    var samples = [CGPoint(x: 0, y: 0)]
    samples.append(contentsOf: Array(repeating: CGPoint(x: 100, y: 40), count: 30))
    let smoothed = smoothCursor(samples, fps: 30)
    let last = smoothed.last ?? .zero
    check(near(last.x, 100, tolerance: 2) && near(last.y, 40, tolerance: 2), "cursor should settle, ended at \(last)")
    check((smoothed.map(\.x).max() ?? 0) < 105, "cursor spring should not overshoot")

    var edit = EditList()
    edit.blurs = [BlurRect(x: 12, y: 24, width: 80, height: 30, radius: 16)]
    edit.sourceSize = CGSize(width: 1920, height: 1200)
    let parsed = try parseEdit(editObject(from: edit))
    check(parsed.zooms == .auto && parsed.style.background == "#14141C" && parsed.blurs.count == 1, "edit list should round-trip")
    let explicit = try parseEdit([
        "zooms": [
            ["t": 1.2, "x": 40, "y": 50, "scale": 1.8, "hold": 0.4],
        ],
    ])
    let resolved = resolvedZooms(edit: explicit, events: [])
    check(resolved.count == 1 && near(resolved[0].scale, 1.8) && near(resolved[0].hold, 0.4), "explicit zooms should replace auto")

    let source = makeSolidImage(width: 40, height: 20, top: (1, 0, 0), bottom: (0, 0, 1))
    var canvas = Canvas()
    canvas.width = 80
    canvas.height = 48
    var renderStyle = Style()
    renderStyle.padding = 4
    renderStyle.shadow = 0
    renderStyle.radius = 0
    let image = try renderFrame(
        source: source,
        canvas: canvas,
        style: renderStyle,
        cursor: CGPoint(x: 4, y: 4),
        focus: CGPoint(x: 20, y: 10),
        zoomScale: 1,
        ripples: [],
        blurs: [],
        context: CIContext()
    )
    let background = pixel(of: image, x: 1, y: 1)
    check(
        abs(Int(background.0) - 20) <= 2 && abs(Int(background.1) - 20) <= 2 && abs(Int(background.2) - 28) <= 2,
        "background pixel \(background)"
    )
    let window = frameLayout(
        sourceSize: CGSize(width: 40, height: 20),
        canvasSize: CGSize(width: 80, height: 48),
        padding: 4,
        focus: CGPoint(x: 20, y: 10),
        zoomScale: 1
    )
    let top = pixel(of: image, x: Int(window.dest.midX), y: Int(window.dest.minY) + 2)
    let bottom = pixel(of: image, x: Int(window.dest.midX), y: Int(window.dest.maxY) - 3)
    check(top.0 > 200 && top.2 < 40, "top of window should be red, got \(top)")
    check(bottom.2 > 200 && bottom.0 < 40, "bottom of window should be blue, got \(bottom)")

    let encoded = makeSolidImage(width: 64, height: 32, top: (1, 0, 0), bottom: (0, 0, 1))
    let url = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("screenreel-orient-\(UUID().uuidString).mp4")
    try writeSingleFrame(encoded, to: url)
    defer { try? FileManager.default.removeItem(at: url) }
    let asset = AVURLAsset(url: url)
    let generator = AVAssetImageGenerator(asset: asset)
    generator.appliesPreferredTrackTransform = true
    var actual = CMTime.zero
    let readBack = try generator.copyCGImage(at: .zero, actualTime: &actual)
    let encodedTop = pixel(of: readBack, x: readBack.width / 2, y: 2)
    let encodedBottom = pixel(of: readBack, x: readBack.width / 2, y: readBack.height - 3)
    check(encodedTop.0 > 200, "encoded top should be red, got \(encodedTop)")
    check(encodedBottom.2 > 200, "encoded bottom should be blue, got \(encodedBottom)")
}

private func writeSingleFrame(_ image: CGImage, to url: URL) throws {
    try? FileManager.default.removeItem(at: url)
    let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
        AVVideoCodecKey: AVVideoCodecType.h264,
        AVVideoWidthKey: image.width,
        AVVideoHeightKey: image.height,
    ])
    let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
        kCVPixelBufferWidthKey as String: image.width,
        kCVPixelBufferHeightKey as String: image.height,
        kCVPixelBufferCGImageCompatibilityKey as String: true,
        kCVPixelBufferCGBitmapContextCompatibilityKey as String: true,
    ])
    writer.add(input)
    guard writer.startWriting() else { throw writer.error ?? ToolError("writer") }
    writer.startSession(atSourceTime: .zero)
    var buffer: CVPixelBuffer?
    guard let pool = adaptor.pixelBufferPool,
          CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) == kCVReturnSuccess,
          let buffer else {
        throw ToolError("pool")
    }
    copyImage(image, into: buffer)
    guard adaptor.append(buffer, withPresentationTime: .zero) else {
        throw writer.error ?? ToolError("append")
    }
    input.markAsFinished()
    let finished = DispatchSemaphore(value: 0)
    writer.finishWriting { finished.signal() }
    if finished.wait(timeout: .now() + 10) != .success {
        throw ToolError("timed out writing the frame")
    }
    if writer.status != .completed {
        throw writer.error ?? ToolError("finish")
    }
}

public func runScreenreelChecks() throws -> Int {
    failures = 0
    do {
        try runChecks()
    } catch {
        FileHandle.standardError.write(Data("FAIL \(error.localizedDescription)\n".utf8))
        return max(failures, 1)
    }
    return failures
}
