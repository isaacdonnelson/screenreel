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
            ["t": 1.2, "x": 40, "y": 50, "scale": 1.8, "hold": 0.4, "id": "manual-1"],
        ],
    ])
    let withClick = [Event(t: 1.0, type: "click", x: 10, y: 10, button: "left")]
    let resolved = resolvedZooms(edit: explicit, events: withClick)
    check(resolved.count == 2, "an added zoom list should keep the auto zoom, got \(resolved.count)")
    check(resolved.contains { $0.id == "manual-1" } && resolved.contains { $0.id == "auto-click-1" }, "auto and manual zooms should both be present")

    let cameraEdit = try parseEdit([
        "zooms": [
            "auto": true,
            "add": [["t": 4.0, "x": 8, "y": 9, "id": "extra"]],
            "remove": ["auto-click-1"],
            "tweak": ["extra": ["scale": 1.25]],
        ],
    ])
    let cameraZooms = resolvedZooms(edit: cameraEdit, events: withClick)
    check(cameraZooms.count == 1 && cameraZooms.first?.id == "extra", "remove and add should leave the manual shot")
    check(near(cameraZooms.first?.scale ?? 0, 1.25), "tweak by id should apply")

    let hand = Cut(start: 4, end: 5, id: "hand-cut", manual: true)
    let autoCut = [Cut(start: 1, end: 2, id: "auto-cut-1")]
    let kept = mergeCuts(newAuto: autoCut, previousAuto: [], existing: [hand], overrides: [])
    check(kept.contains { $0.id == "hand-cut" } && kept.contains { $0.id == "auto-cut-1" }, "plan should keep a hand-added cut")
    let replanned = mergeCuts(
        newAuto: [Cut(start: 1.5, end: 2.5, id: "auto-cut-1")],
        previousAuto: autoCut,
        existing: kept,
        overrides: [Cut(start: 8, end: 9, id: "override-cut")]
    )
    check(replanned.contains { $0.id == "hand-cut" }, "replan keeps the hand cut")
    check(replanned.contains { $0.id == "override-cut" }, "replan keeps overrides")
    check(replanned.contains { $0.id == "auto-cut-1" && near($0.start, 1.5) }, "auto cut is regenerated")
    check(!replanned.contains { near($0.start, 1) && near($0.end, 2) }, "the old auto span is not kept beside the new one")

    let fast = buildTimeMap(duration: 12, segments: [Segment(start: 0, end: 12, speed: 6)], cuts: [])
    let outputZooms = zoomsOnOutputClock(
        [Zoom(t: 1, x: 400, y: 300, scale: 1.6, hold: 0.6)],
        map: fast,
        style: style
    )
    check(outputZooms[0].easeIn >= 0.6, "zoom ease stays on the output clock, got \(outputZooms[0].easeIn)")
    let midway = outputZoomStrength(at: outputZooms[0].start + outputZooms[0].easeIn * 0.5, zoom: outputZooms[0])
    check(near(midway, 0.5, tolerance: 0.02), "half of the output ease should be half strength, got \(midway)")
    var peakRate = 0.0
    var previousScale = 1.0
    let frames = Int((outputZooms[0].easeIn * 60).rounded(.up))
    for frame in 1...frames {
        let time = outputZooms[0].start + Double(frame) / 60
        let strength = outputZoomStrength(at: time, zoom: outputZooms[0])
        let scale = 1 + (outputZooms[0].scale - 1) * strength
        let rate = abs(log(scale) - log(previousScale)) * 60
        peakRate = max(peakRate, rate)
        previousScale = scale
    }
    check(peakRate <= 1.21, "zoom rate should stay near 1.2 /s, got \(peakRate)")
    var visibleRipple = 0
    let clickAt = 2.0
    for frame in 0..<Int(fast.outputDuration * 60) {
        let age = rippleAge(outputTime: Double(frame) / 60, eventSourceTime: clickAt, map: fast)
        if age >= 0 && age <= 0.45 { visibleRipple += 1 }
    }
    check(visibleRipple >= 24, "a ripple inside a 6x ramp should still last 24 frames, got \(visibleRipple)")

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

    let patterned = makeCheckerImage(width: 200, height: 120)
    var blurCanvas = Canvas()
    blurCanvas.width = 200
    blurCanvas.height = 120
    var blurStyle = Style()
    blurStyle.padding = 0
    blurStyle.shadow = 0
    blurStyle.radius = 0
    blurStyle.background = "#000000"
    let blurred = try renderFrame(
        source: patterned,
        canvas: blurCanvas,
        style: blurStyle,
        cursor: nil,
        focus: CGPoint(x: 100, y: 60),
        zoomScale: 1,
        ripples: [],
        blurs: [BlurRect(x: 0, y: 0, width: 100, height: 50, radius: 8)],
        context: CIContext()
    )
    let topLeft = pixel(of: blurred, x: 10, y: 10)
    let bottomLeft = pixel(of: blurred, x: 10, y: 100)
    check(topLeft.0 > 40 && topLeft.1 > 40, "rect (0, 0, 100, 50) should blur the top-left checker, got \(topLeft)")
    check(bottomLeft.2 > 200 && bottomLeft.0 < 40, "the same rect should leave the bottom-left blue, got \(bottomLeft)")
    let zoomed = try renderFrame(
        source: patterned,
        canvas: blurCanvas,
        style: blurStyle,
        cursor: nil,
        focus: CGPoint(x: 40, y: 20),
        zoomScale: 2,
        ripples: [],
        blurs: [BlurRect(x: 0, y: 0, width: 100, height: 50, radius: 8)],
        context: CIContext()
    )
    let zoomedTop = pixel(of: zoomed, x: zoomed.width / 2, y: 30)
    check(zoomedTop.0 > 40 && zoomedTop.1 > 40, "a zoom should not reveal the blurred top-left, got \(zoomedTop)")

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
