import CoreGraphics
import Foundation

struct Canvas: Equatable {
    var width: Int = 1920
    var height: Int = 1080
    var fps: Int = 30
}

struct Style: Equatable {
    var background: String = "#14141C"
    var padding: Double = 72
    var radius: Double = 20
    var shadow: Double = 48
    var cursorScale: Double = 1.6
    var zoomScale: Double = 1.45
    var zoomIn: Double = 0.35
    var zoomHold: Double = 0.6
    var zoomOut: Double = 0.4
}

struct BlurRect: Equatable {
    var x: Double
    var y: Double
    var width: Double
    var height: Double
    var radius: Double
    var t0: Double? = nil
    var t1: Double? = nil
}

struct Zoom: Equatable {
    var t: Double
    var x: Double
    var y: Double
    var scale: Double
    var hold: Double
    var id: String? = nil
}

struct ZoomTweak: Equatable {
    var t: Double?
    var x: Double?
    var y: Double?
    var scale: Double?
    var hold: Double?
}

/// Manual shots are added on top of auto zooms. A list never turns auto off.
struct CameraSpec: Equatable {
    var auto: Bool = true
    var add: [Zoom] = []
    var remove: [String] = []
    var tweak: [String: ZoomTweak] = [:]
}

enum Zooms: Equatable {
    case auto
    case explicit([Zoom])
    case camera(CameraSpec)
}

struct Event: Equatable {
    var t: Double
    var type: String
    var x: Double
    var y: Double
    var button: String?
    var label: String? = nil
    var dx: Double? = nil
    var dy: Double? = nil
    var w: Double? = nil
    var h: Double? = nil
    var t1: Double? = nil
}

struct EditList {
    var version: Int = 1
    var source: String = "raw.mov"
    var events: String = "events.jsonl"
    var canvas: Canvas = Canvas()
    var style: Style = Style()
    var blurs: [BlurRect] = []
    var covers: [CoverRect] = []
    var segments: [Segment] = []
    var cuts: [Cut] = []
    var steps: [StepFit] = []
    var zooms: Zooms = .auto
    var sourceSize: CGSize?
    var passthrough: [String: Any] = [:]
}

struct DisplayMap: Equatable {
    var topLeftX: Double
    var topLeftY: Double
    var pointWidth: Double
    var pointHeight: Double
    var pixelWidth: Double
    var pixelHeight: Double

    func pixel(eventX: Double, eventY: Double) -> (Double, Double) {
        let scaleX = pixelWidth / max(pointWidth, 1)
        let scaleY = pixelHeight / max(pointHeight, 1)
        return ((eventX - topLeftX) * scaleX, (eventY - topLeftY) * scaleY)
    }
}

/// CGDisplayBounds uses a bottom-left origin. CGEvent.location uses a top-left origin on the main display.
func displayTopLeft(displayBounds: CGRect, mainBounds: CGRect) -> CGPoint {
    let x = displayBounds.origin.x - mainBounds.origin.x
    let y = mainBounds.height - (displayBounds.origin.y - mainBounds.origin.y + displayBounds.height)
    return CGPoint(x: x, y: y)
}

func smoothstep(_ value: Double) -> Double {
    let t = min(1, max(0, value))
    return t * t * (3 - 2 * t)
}

func zoomStrength(at time: Double, zoom: Zoom, style: Style) -> Double {
    let fadeIn = max(0, style.zoomIn)
    let hold = max(0, zoom.hold)
    let fadeOut = max(0, style.zoomOut)
    let end = zoom.t + fadeIn + hold + fadeOut
    if time < zoom.t || time >= end { return 0 }
    if time < zoom.t + fadeIn {
        if fadeIn == 0 { return 1 }
        return smoothstep((time - zoom.t) / fadeIn)
    }
    if time < zoom.t + fadeIn + hold { return 1 }
    if fadeOut == 0 { return 0 }
    let progress = (time - zoom.t - fadeIn - hold) / fadeOut
    return 1 - smoothstep(progress)
}

func camera(at time: Double, zooms: [Zoom], style: Style, cursor: CGPoint) -> (focus: CGPoint, scale: Double) {
    var best = 0.0
    var chosen: Zoom?
    for zoom in zooms {
        let strength = zoomStrength(at: time, zoom: zoom, style: style)
        if strength > best {
            best = strength
            chosen = zoom
        }
    }
    guard let chosen, best > 0 else { return (cursor, 1) }
    let focus = CGPoint(
        x: cursor.x + (chosen.x - cursor.x) * best,
        y: cursor.y + (chosen.y - cursor.y) * best
    )
    let scale = 1 + (max(1, chosen.scale) - 1) * best
    return (focus, scale)
}

func frameLayout(sourceSize: CGSize, canvasSize: CGSize, padding: Double, focus: CGPoint, zoomScale: Double) -> (crop: CGRect, dest: CGRect) {
    let safePad = max(0, min(padding, Double(min(canvasSize.width, canvasSize.height)) / 4))
    let available = CGRect(
        x: safePad,
        y: safePad,
        width: max(2, Double(canvasSize.width) - safePad * 2),
        height: max(2, Double(canvasSize.height) - safePad * 2)
    )
    let zoom = max(1, zoomScale)
    var cropWidth = Double(sourceSize.width) / zoom
    var cropHeight = Double(sourceSize.height) / zoom
    cropWidth = min(max(1, cropWidth), Double(sourceSize.width))
    cropHeight = min(max(1, cropHeight), Double(sourceSize.height))
    var originX = Double(focus.x) - cropWidth / 2
    var originY = Double(focus.y) - cropHeight / 2
    originX = min(max(0, originX), Double(sourceSize.width) - cropWidth)
    originY = min(max(0, originY), Double(sourceSize.height) - cropHeight)
    let crop = CGRect(x: originX, y: originY, width: cropWidth, height: cropHeight)

    let aspect = cropWidth / cropHeight
    let availableAspect = available.width / available.height
    let fittedWidth: Double
    let fittedHeight: Double
    if aspect > availableAspect {
        fittedWidth = available.width
        fittedHeight = available.width / aspect
    } else {
        fittedHeight = available.height
        fittedWidth = available.height * aspect
    }
    let dest = CGRect(
        x: available.midX - fittedWidth / 2,
        y: available.midY - fittedHeight / 2,
        width: fittedWidth,
        height: fittedHeight
    )
    return (crop, dest)
}

func smoothCursor(_ samples: [CGPoint], fps: Double) -> [CGPoint] {
    guard var position = samples.first else { return [] }
    var velocity = CGPoint.zero
    let omega = 16.0
    let step = 1 / max(fps, 1)
    var smoothed: [CGPoint] = []
    smoothed.reserveCapacity(samples.count)
    for target in samples {
        let accelerationX = omega * omega * (target.x - position.x) - 2 * omega * velocity.x
        let accelerationY = omega * omega * (target.y - position.y) - 2 * omega * velocity.y
        velocity.x += accelerationX * step
        velocity.y += accelerationY * step
        position.x += velocity.x * step
        position.y += velocity.y * step
        smoothed.append(position)
    }
    return smoothed
}

func cursorTrack(events: [Event], frameCount: Int, fps: Double, fallback: CGPoint) -> [CGPoint] {
    let points = events.filter { $0.type == "move" || $0.type == "click" }
    var index = 0
    var current = points.first.map { CGPoint(x: $0.x, y: $0.y) } ?? fallback
    var raw: [CGPoint] = []
    raw.reserveCapacity(frameCount)
    for frame in 0..<frameCount {
        let time = Double(frame) / max(fps, 1)
        while index < points.count && points[index].t <= time + 0.0001 {
            current = CGPoint(x: points[index].x, y: points[index].y)
            index += 1
        }
        raw.append(current)
    }
    return smoothCursor(raw, fps: fps)
}

func autoZooms(events: [Event], style: Style) -> [Zoom] {
    var zooms: [Zoom] = []
    var cursor = CGPoint.zero
    var burstStart: Double?
    var burstCursor = CGPoint.zero
    var lastKey: Double?

    func recentlyZoomed(_ time: Double) -> Bool {
        guard let last = zooms.last else { return false }
        return time - last.t < 0.2
    }

    var clickCount = 0
    var keyCount = 0

    func closeBurst() {
        guard let start = burstStart else { return }
        keyCount += 1
        zooms.append(Zoom(
            t: start,
            x: burstCursor.x,
            y: burstCursor.y,
            scale: style.zoomScale,
            hold: style.zoomHold,
            id: "auto-key-\(keyCount)"
        ))
        burstStart = nil
        lastKey = nil
    }

    for event in events.sorted(by: { $0.t < $1.t }) {
        switch event.type {
        case "move":
            cursor = CGPoint(x: event.x, y: event.y)
        case "click", "down":
            if let start = burstStart, event.t - start > 0.15 {
                closeBurst()
            } else {
                burstStart = nil
                lastKey = nil
            }
            cursor = CGPoint(x: event.x, y: event.y)
            if !recentlyZoomed(event.t) {
                clickCount += 1
                zooms.append(Zoom(
                    t: event.t,
                    x: event.x,
                    y: event.y,
                    scale: style.zoomScale,
                    hold: style.zoomHold,
                    id: "auto-click-\(clickCount)"
                ))
            }
        case "key", "scroll":
            if let previous = lastKey, burstStart != nil, event.t - previous <= 0.4 {
                lastKey = event.t
            } else {
                if burstStart != nil { closeBurst() }
                if recentlyZoomed(event.t) {
                    burstStart = nil
                    lastKey = nil
                } else {
                    burstStart = event.t
                    burstCursor = cursor
                    lastKey = event.t
                }
            }
        default:
            break
        }
    }
    closeBurst()
    return zooms
}

func resolvedZooms(edit: EditList, events: [Event]) -> [Zoom] {
    let generated = autoZooms(events: events, style: edit.style)
    switch edit.zooms {
    case .auto:
        return generated
    case .explicit(let zooms):
        return mergeZooms(auto: generated, spec: CameraSpec(auto: true, add: zooms), style: edit.style)
    case .camera(let spec):
        return mergeZooms(auto: generated, spec: spec, style: edit.style)
    }
}

func mergeZooms(auto: [Zoom], spec: CameraSpec, style: Style) -> [Zoom] {
    var result = spec.auto ? auto : []
    let removed = Set(spec.remove)
    result.removeAll { zoom in
        guard let id = zoom.id else { return false }
        return removed.contains(id)
    }
    for (index, extra) in spec.add.enumerated() {
        var zoom = extra
        if zoom.scale <= 0 { zoom.scale = style.zoomScale }
        if zoom.id == nil { zoom.id = "add-\(index + 1)" }
        result.append(zoom)
    }
    for id in spec.tweak.keys.sorted() {
        guard let tweak = spec.tweak[id], let index = result.firstIndex(where: { $0.id == id }) else { continue }
        if let t = tweak.t { result[index].t = t }
        if let x = tweak.x { result[index].x = x }
        if let y = tweak.y { result[index].y = y }
        if let scale = tweak.scale { result[index].scale = scale }
        if let hold = tweak.hold { result[index].hold = hold }
    }
    return result
}

func quinticEase(_ value: Double) -> Double {
    let t = min(1, max(0, value))
    return t * t * t * (t * (t * 6 - 15) + 10)
}

struct OutputZoom {
    var start: Double
    var focus: CGPoint
    var scale: Double
    var easeIn: Double
    var hold: Double
    var easeOut: Double
}

/// Ease long enough that a quintic zoom stays under 1.2 /s on ln(scale).
func outputEaseDuration(from startScale: Double, to endScale: Double, requested: Double) -> Double {
    let delta = abs(log(max(endScale, 1.000001)) - log(max(startScale, 1.000001)))
    let limited = delta * 1.875 / 1.2 + 0.004
    return max(0.6, max(requested, limited))
}

func outputZoomStrength(at time: Double, zoom: OutputZoom) -> Double {
    let end = zoom.start + zoom.easeIn + zoom.hold + zoom.easeOut
    if time < zoom.start || time >= end { return 0 }
    if time < zoom.start + zoom.easeIn {
        if zoom.easeIn == 0 { return 1 }
        return quinticEase((time - zoom.start) / zoom.easeIn)
    }
    if time < zoom.start + zoom.easeIn + zoom.hold { return 1 }
    if zoom.easeOut == 0 { return 0 }
    let progress = (time - zoom.start - zoom.easeIn - zoom.hold) / zoom.easeOut
    return 1 - quinticEase(progress)
}

func zoomsOnOutputClock(_ zooms: [Zoom], map: TimeMap, style: Style) -> [OutputZoom] {
    zooms.map { zoom in
        let scale = max(1, zoom.scale)
        return OutputZoom(
            start: map.outputTime(at: zoom.t),
            focus: CGPoint(x: zoom.x, y: zoom.y),
            scale: scale,
            easeIn: outputEaseDuration(from: 1, to: scale, requested: max(style.zoomIn, 0.6)),
            hold: max(0, zoom.hold),
            easeOut: outputEaseDuration(from: scale, to: 1, requested: max(style.zoomOut, 0.6))
        )
    }
}

func cameraOutput(at time: Double, zooms: [OutputZoom], cursor: CGPoint) -> (focus: CGPoint, scale: Double) {
    var best = 0.0
    var chosen: OutputZoom?
    for zoom in zooms {
        let strength = outputZoomStrength(at: time, zoom: zoom)
        if strength > best {
            best = strength
            chosen = zoom
        }
    }
    guard let chosen, best > 0 else { return (cursor, 1) }
    let focus = CGPoint(
        x: cursor.x + (chosen.focus.x - cursor.x) * best,
        y: cursor.y + (chosen.focus.y - cursor.y) * best
    )
    let scale = 1 + (chosen.scale - 1) * best
    return (focus, scale)
}

func cursorTrackOutput(events: [Event], frameCount: Int, fps: Double, map: TimeMap, fallback: CGPoint) -> [CGPoint] {
    let points = events.filter { $0.type == "move" || $0.type == "click" || $0.type == "down" }
    var raw: [CGPoint] = []
    raw.reserveCapacity(frameCount)
    for frame in 0..<frameCount {
        let time = map.sourceTime(at: Double(frame) / max(fps, 1))
        var current = points.first.map { CGPoint(x: $0.x, y: $0.y) } ?? fallback
        for point in points where point.t <= time + 0.0001 {
            current = CGPoint(x: point.x, y: point.y)
        }
        raw.append(current)
    }
    return smoothCursor(raw, fps: fps)
}

func parseColor(_ hex: String) throws -> (CGFloat, CGFloat, CGFloat) {
    var text = hex.trimmingCharacters(in: .whitespacesAndNewlines)
    if text.hasPrefix("#") { text.removeFirst() }
    guard text.count == 6, let value = UInt32(text, radix: 16) else {
        throw ToolError("background must be a hex color like #14141C")
    }
    let red = CGFloat((value >> 16) & 0xFF) / 255
    let green = CGFloat((value >> 8) & 0xFF) / 255
    let blue = CGFloat(value & 0xFF) / 255
    return (red, green, blue)
}

func editObject(from edit: EditList) -> [String: Any] {
    var object: [String: Any] = [
        "version": edit.version,
        "source": edit.source,
        "events": edit.events,
        "canvas": [
            "width": edit.canvas.width,
            "height": edit.canvas.height,
            "fps": edit.canvas.fps,
        ],
        "style": styleObject(edit.style),
        "blurs": edit.blurs.map { blurObject($0) },
        "covers": edit.covers.map { coverObject($0) },
        "segments": edit.segments.map { ["start": rounded3($0.start), "end": rounded3($0.end), "speed": rounded3($0.speed)] },
        "cuts": edit.cuts.map { cutObject($0) },
        "steps": edit.steps.map { ["label": $0.label, "target": rounded3($0.target)] },
    ]
    for (key, value) in edit.passthrough where object[key] == nil {
        object[key] = value
    }
    switch edit.zooms {
    case .auto:
        object["zooms"] = "auto"
    case .explicit(let zooms):
        object["zooms"] = zooms.map { zoomObject($0) }
    case .camera(let spec):
        object["zooms"] = cameraObject(spec)
    }
    if let sourceSize = edit.sourceSize, sourceSize.width > 0, sourceSize.height > 0 {
        object["sourceSize"] = [
            "width": Int(sourceSize.width.rounded()),
            "height": Int(sourceSize.height.rounded()),
        ]
    }
    return object
}

func parseEdit(_ object: [String: Any]) throws -> EditList {
    var edit = EditList()
    if let version = jsonInt(object["version"]) { edit.version = version }
    if let source = object["source"] as? String, !source.isEmpty { edit.source = source }
    if let events = object["events"] as? String, !events.isEmpty { edit.events = events }
    if let canvas = object["canvas"] as? [String: Any] {
        if let width = jsonInt(canvas["width"]), width >= 2 { edit.canvas.width = width }
        if let height = jsonInt(canvas["height"]), height >= 2 { edit.canvas.height = height }
        if let fps = jsonInt(canvas["fps"]), fps > 0 { edit.canvas.fps = fps }
    }
    if let style = object["style"] as? [String: Any] {
        edit.style = parseStyle(style, defaults: edit.style)
    }
    if let blurs = object["blurs"] as? [[String: Any]] {
        edit.blurs = try blurs.map { try parseBlur($0) }
    }
    if let covers = object["covers"] as? [[String: Any]] {
        edit.covers = try covers.map { try parseCover($0) }
    }
    if let segments = object["segments"] as? [[String: Any]] {
        edit.segments = try segments.map { try parseSegment($0) }
    }
    if let cuts = object["cuts"] as? [[String: Any]] {
        edit.cuts = cuts.compactMap { cut in
            guard let start = jsonDouble(cut["start"]), let end = jsonDouble(cut["end"]) else { return nil }
            return Cut(start: start, end: end, id: cut["id"] as? String, manual: cut["manual"] as? Bool ?? false)
        }
    }
    if let steps = object["steps"] as? [[String: Any]] {
        edit.steps = steps.compactMap { step in
            guard let label = step["label"] as? String, let target = jsonDouble(step["target"]) else { return nil }
            return StepFit(label: label, target: target)
        }
    }
    for key in ["audio", "overlays", "duck", "lufs"] {
        if let value = object[key] { edit.passthrough[key] = value }
    }
    if let zooms = object["zooms"] as? String {
        guard zooms == "auto" else {
            throw ToolError("zooms must be \"auto\" or a list of {t, x, y, scale, hold}")
        }
        edit.zooms = .auto
    } else if let zooms = object["zooms"] as? [[String: Any]] {
        edit.zooms = .explicit(try zooms.map { try parseZoom($0, style: edit.style) })
    } else if let zooms = object["zooms"] as? [String: Any] {
        edit.zooms = .camera(try parseCamera(zooms, style: edit.style))
    } else if object["zooms"] != nil {
        throw ToolError("zooms must be \"auto\" or a list of {t, x, y, scale, hold}")
    }
    if let size = object["sourceSize"] as? [String: Any],
       let width = jsonDouble(size["width"]),
       let height = jsonDouble(size["height"]) {
        edit.sourceSize = CGSize(width: width, height: height)
    }
    try _ = parseColor(edit.style.background)
    return edit
}

func loadEdit(at url: URL) throws -> EditList {
    guard let object = readJSONObject(at: url) else {
        throw ToolError("Could not read \(url.path)")
    }
    return try parseEdit(object)
}

func loadEvents(at url: URL) throws -> [Event] {
    guard let text = try? String(contentsOf: url, encoding: .utf8) else {
        throw ToolError("Could not read \(url.path)")
    }
    var events: [Event] = []
    for rawLine in text.split(separator: "\n", omittingEmptySubsequences: true) {
        let data = Data(rawLine.utf8)
        guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
        guard let type = object["type"] as? String, let time = jsonDouble(object["t"]) else { continue }
        events.append(Event(
            t: time,
            type: type,
            x: jsonDouble(object["x"]) ?? 0,
            y: jsonDouble(object["y"]) ?? 0,
            button: object["button"] as? String,
            label: object["label"] as? String,
            dx: jsonDouble(object["dx"]),
            dy: jsonDouble(object["dy"]),
            w: jsonDouble(object["w"]) ?? jsonDouble(object["width"]),
            h: jsonDouble(object["h"]) ?? jsonDouble(object["height"]),
            t1: jsonDouble(object["t1"])
        ))
    }
    return events.sorted { $0.t < $1.t }
}

func zoomObject(_ zoom: Zoom) -> [String: Any] {
    var object: [String: Any] = [
        "t": (zoom.t * 1000).rounded() / 1000,
        "x": (zoom.x * 10).rounded() / 10,
        "y": (zoom.y * 10).rounded() / 10,
        "scale": (zoom.scale * 100).rounded() / 100,
        "hold": (zoom.hold * 1000).rounded() / 1000,
    ]
    if let id = zoom.id { object["id"] = id }
    return object
}

private func cameraObject(_ spec: CameraSpec) -> [String: Any] {
    var tweak: [String: [String: Any]] = [:]
    for id in spec.tweak.keys.sorted() {
        let value = spec.tweak[id]!
        var fields: [String: Any] = [:]
        if let t = value.t { fields["t"] = t }
        if let x = value.x { fields["x"] = x }
        if let y = value.y { fields["y"] = y }
        if let scale = value.scale { fields["scale"] = scale }
        if let hold = value.hold { fields["hold"] = hold }
        tweak[id] = fields
    }
    return [
        "auto": spec.auto,
        "add": spec.add.map { zoomObject($0) },
        "remove": spec.remove,
        "tweak": tweak,
    ]
}

private func cutObject(_ cut: Cut) -> [String: Any] {
    var object: [String: Any] = ["start": rounded3(cut.start), "end": rounded3(cut.end)]
    if let id = cut.id { object["id"] = id }
    if cut.manual { object["manual"] = true }
    return object
}

private func styleObject(_ style: Style) -> [String: Any] {
    [
        "background": style.background,
        "padding": style.padding,
        "radius": style.radius,
        "shadow": style.shadow,
        "cursorScale": style.cursorScale,
        "zoomScale": style.zoomScale,
        "zoomIn": style.zoomIn,
        "zoomHold": style.zoomHold,
        "zoomOut": style.zoomOut,
    ]
}

private func rounded3(_ value: Double) -> Double {
    (value * 1000).rounded() / 1000
}

private func blurObject(_ blur: BlurRect) -> [String: Any] {
    var object: [String: Any] = [
        "x": blur.x,
        "y": blur.y,
        "width": blur.width,
        "height": blur.height,
        "radius": blur.radius,
    ]
    if let t0 = blur.t0 { object["t0"] = rounded3(t0) }
    if let t1 = blur.t1 { object["t1"] = rounded3(t1) }
    return object
}

private func coverObject(_ cover: CoverRect) -> [String: Any] {
    var object: [String: Any] = [
        "x": cover.x,
        "y": cover.y,
        "width": cover.width,
        "height": cover.height,
    ]
    if let t0 = cover.t0 { object["t0"] = rounded3(t0) }
    if let t1 = cover.t1 { object["t1"] = rounded3(t1) }
    if let color = cover.color { object["color"] = color }
    return object
}

private func parseStyle(_ object: [String: Any], defaults: Style) -> Style {
    var style = defaults
    if let background = object["background"] as? String, !background.isEmpty {
        style.background = background
    }
    if let padding = jsonDouble(object["padding"]) { style.padding = padding }
    if let radius = jsonDouble(object["radius"]) { style.radius = radius }
    if let shadow = jsonDouble(object["shadow"]) { style.shadow = shadow }
    if let cursorScale = jsonDouble(object["cursorScale"]) { style.cursorScale = cursorScale }
    if let zoomScale = jsonDouble(object["zoomScale"]) { style.zoomScale = zoomScale }
    if let zoomIn = jsonDouble(object["zoomIn"]) { style.zoomIn = zoomIn }
    if let zoomHold = jsonDouble(object["zoomHold"]) { style.zoomHold = zoomHold }
    if let zoomOut = jsonDouble(object["zoomOut"]) { style.zoomOut = zoomOut }
    return style
}

private func parseZoom(_ object: [String: Any], style: Style) throws -> Zoom {
    guard let time = jsonDouble(object["t"]),
          let x = jsonDouble(object["x"]),
          let y = jsonDouble(object["y"]) else {
        throw ToolError("Each zoom needs t, x, and y")
    }
    return Zoom(
        t: time,
        x: x,
        y: y,
        scale: jsonDouble(object["scale"]) ?? style.zoomScale,
        hold: jsonDouble(object["hold"]) ?? style.zoomHold,
        id: object["id"] as? String
    )
}

private func parseCamera(_ object: [String: Any], style: Style) throws -> CameraSpec {
    var spec = CameraSpec()
    if let auto = object["auto"] as? Bool { spec.auto = auto }
    if let add = object["add"] as? [[String: Any]] {
        spec.add = try add.map { try parseZoom($0, style: style) }
    }
    if let remove = object["remove"] as? [String] { spec.remove = remove }
    if let tweak = object["tweak"] as? [String: [String: Any]] {
        for id in tweak.keys.sorted() {
            let fields = tweak[id] ?? [:]
            spec.tweak[id] = ZoomTweak(
                t: jsonDouble(fields["t"]),
                x: jsonDouble(fields["x"]),
                y: jsonDouble(fields["y"]),
                scale: jsonDouble(fields["scale"]),
                hold: jsonDouble(fields["hold"])
            )
        }
    }
    return spec
}

private func parseBlur(_ object: [String: Any]) throws -> BlurRect {
    guard let x = jsonDouble(object["x"]),
          let y = jsonDouble(object["y"]),
          let width = jsonDouble(object["width"]),
          let height = jsonDouble(object["height"]) else {
        throw ToolError("Each blur needs x, y, width, and height in source pixels")
    }
    return BlurRect(
        x: x,
        y: y,
        width: width,
        height: height,
        radius: jsonDouble(object["radius"]) ?? 18,
        t0: jsonDouble(object["t0"]),
        t1: jsonDouble(object["t1"])
    )
}

private func parseCover(_ object: [String: Any]) throws -> CoverRect {
    guard let x = jsonDouble(object["x"]),
          let y = jsonDouble(object["y"]),
          let width = jsonDouble(object["width"]),
          let height = jsonDouble(object["height"]) else {
        throw ToolError("Each cover needs x, y, width, and height in source pixels")
    }
    return CoverRect(
        x: x,
        y: y,
        width: width,
        height: height,
        t0: jsonDouble(object["t0"]),
        t1: jsonDouble(object["t1"]),
        color: object["color"] as? String
    )
}

private func parseSegment(_ object: [String: Any]) throws -> Segment {
    guard let start = jsonDouble(object["start"]),
          let end = jsonDouble(object["end"]) else {
        throw ToolError("Each segment needs start and end")
    }
    return Segment(start: start, end: end, speed: jsonDouble(object["speed"]) ?? 1)
}

func resolveTakeFile(_ name: String, take: URL) -> URL {
    if name.hasPrefix("/") { return URL(fileURLWithPath: name) }
    return take.appendingPathComponent(name)
}
