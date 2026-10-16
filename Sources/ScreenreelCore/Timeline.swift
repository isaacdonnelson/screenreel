import AVFoundation
import CoreGraphics
import Foundation

struct Segment: Equatable {
    var start: Double
    var end: Double
    var speed: Double
}

struct Cut: Equatable {
    var start: Double
    var end: Double
}

struct CoverRect: Equatable {
    var x: Double
    var y: Double
    var width: Double
    var height: Double
    var t0: Double?
    var t1: Double?
    var color: String?
}

struct StepFit: Equatable {
    var label: String
    var target: Double
}

struct Piece: Equatable {
    var sourceStart: Double
    var sourceEnd: Double
    var speed: Double
    var outputStart: Double
}

struct TimeMap: Equatable {
    var pieces: [Piece]
    var outputDuration: Double

    func sourceTime(at output: Double) -> Double {
        let clamped = min(max(0, output), outputDuration)
        for piece in pieces {
            let length = (piece.sourceEnd - piece.sourceStart) / max(piece.speed, 0.25)
            if clamped <= piece.outputStart + length + 0.0001 {
                let into = max(0, clamped - piece.outputStart)
                return piece.sourceStart + into * piece.speed
            }
        }
        return pieces.last?.sourceEnd ?? clamped
    }

    func outputTime(at source: Double) -> Double {
        for piece in pieces {
            if source <= piece.sourceEnd + 0.0001 {
                if source < piece.sourceStart { return piece.outputStart }
                let into = max(0, source - piece.sourceStart)
                return piece.outputStart + into / max(piece.speed, 0.25)
            }
        }
        return outputDuration
    }
}

func buildTimeMap(duration: Double, segments: [Segment], cuts: [Cut]) -> TimeMap {
    let safe = max(0, duration)
    if segments.isEmpty && cuts.isEmpty {
        let piece = Piece(sourceStart: 0, sourceEnd: safe, speed: 1, outputStart: 0)
        return TimeMap(pieces: [piece], outputDuration: safe)
    }
    var intervals: [(Double, Double)] = [(0, safe)]
    for cut in cuts.sorted(by: { $0.start < $1.start }) {
        let cutStart = min(max(0, cut.start), safe)
        let cutEnd = min(max(0, cut.end), safe)
        if cutEnd <= cutStart { continue }
        var next: [(Double, Double)] = []
        for interval in intervals {
            if cutEnd <= interval.0 || cutStart >= interval.1 {
                next.append(interval)
                continue
            }
            if cutStart > interval.0 { next.append((interval.0, cutStart)) }
            if cutEnd < interval.1 { next.append((cutEnd, interval.1)) }
        }
        intervals = next
    }
    var pieces: [Piece] = []
    var output = 0.0
    for interval in intervals where interval.1 - interval.0 > 0.0001 {
        for part in splitInterval(interval.0, interval.1, segments: segments) {
            let speed = max(0.25, part.speed)
            pieces.append(Piece(sourceStart: part.start, sourceEnd: part.end, speed: speed, outputStart: output))
            output += (part.end - part.start) / speed
        }
    }
    if pieces.isEmpty {
        return TimeMap(pieces: [Piece(sourceStart: 0, sourceEnd: 0, speed: 1, outputStart: 0)], outputDuration: 0)
    }
    return TimeMap(pieces: pieces, outputDuration: output)
}

func plannedTiming(events: [Event], duration: Double, steps: [StepFit]) -> (segments: [Segment], cuts: [Cut]) {
    let markers = events.filter { $0.type == "marker" }.sorted { $0.t < $1.t }
    let busy = busyIntervals(actionTimes(events))
    guard !markers.isEmpty, !steps.isEmpty else {
        return timingForRange(0, duration, busy: busy, target: nil)
    }
    var segments: [Segment] = []
    var cuts: [Cut] = []
    if let first = markers.first, first.t > 0.05 {
        let head = timingForRange(0, first.t, busy: busy, target: nil)
        segments.append(contentsOf: head.segments)
        cuts.append(contentsOf: head.cuts)
    }
    for (index, marker) in markers.enumerated() {
        let end = index + 1 < markers.count ? markers[index + 1].t : duration
        let target = steps.first { $0.label == (marker.label ?? "") }?.target
        let piece = timingForRange(marker.t, end, busy: busy, target: target)
        segments.append(contentsOf: piece.segments)
        cuts.append(contentsOf: piece.cuts)
    }
    return (segments, cuts)
}

func stepTimeline(events: [Event], duration: Double, map: TimeMap) -> [[String: Any]] {
    let markers = events.filter { $0.type == "marker" }.sorted { $0.t < $1.t }
    guard !markers.isEmpty else { return [] }
    var steps: [[String: Any]] = []
    for (index, marker) in markers.enumerated() {
        let end = index + 1 < markers.count ? markers[index + 1].t : duration
        steps.append([
            "label": marker.label ?? "",
            "sourceT0": rounded(marker.t),
            "sourceT1": rounded(end),
            "t0": rounded(map.outputTime(at: marker.t)),
            "t1": rounded(map.outputTime(at: end)),
        ])
    }
    return steps
}

func collectWarnings(events: [Event], edit: EditList, duration: Double, sourceSize: CGSize) -> [String] {
    var warnings: [String] = []
    let width = sourceSize.width
    let height = sourceSize.height
    if width > 1, height > 1 {
        for zoom in resolvedZooms(edit: edit, events: events) where zoom.x < 0 || zoom.y < 0 || zoom.x > width || zoom.y > height {
            warnings.append(String(format: "Zoom at %.2fs is outside the frame", zoom.t))
        }
        let bounds = CGRect(x: 0, y: 0, width: width, height: height)
        for blur in edit.blurs {
            let rect = CGRect(x: blur.x, y: blur.y, width: blur.width, height: blur.height)
            if !rect.intersects(bounds) {
                warnings.append("A blur rect does not cover the picture")
                break
            }
        }
        let pointers = events.filter { $0.type == "move" || $0.type == "down" || $0.type == "click" }
        if !pointers.isEmpty && pointers.allSatisfy({ $0.x < 0 || $0.y < 0 || $0.x > width || $0.y > height }) {
            warnings.append("Cursor positions are outside the frame")
        }
    }
    var previous = 0.0
    for time in actionTimes(events) + [duration] {
        if time - previous > 8 {
            warnings.append(String(format: "No actions from %.1fs to %.1fs", previous, time))
        }
        previous = time
    }
    return warnings
}

func activeNow<T>(_ items: [T], time: Double, start: (T) -> Double?, end: (T) -> Double?) -> [T] {
    items.filter { item in
        let open = start(item) ?? 0
        let close = end(item) ?? Double.greatestFiniteMagnitude
        return time >= open && time < close
    }
}

private func rounded(_ value: Double) -> Double {
    (value * 1000).rounded() / 1000
}

private func splitInterval(_ start: Double, _ end: Double, segments: [Segment]) -> [(start: Double, end: Double, speed: Double)] {
    var bounds = [start, end]
    for segment in segments where segment.end > start && segment.start < end {
        bounds.append(min(max(segment.start, start), end))
        bounds.append(min(max(segment.end, start), end))
    }
    let points = Array(Set(bounds.map { ($0 * 1000).rounded() / 1000 })).sorted()
    var parts: [(start: Double, end: Double, speed: Double)] = []
    for index in 0..<(points.count - 1) {
        let partStart = points[index]
        let partEnd = points[index + 1]
        if partEnd - partStart < 0.0001 { continue }
        let mid = (partStart + partEnd) / 2
        let speed = segments.first { mid >= $0.start - 0.0001 && mid < $0.end }?.speed ?? 1
        parts.append((partStart, partEnd, speed))
    }
    return parts
}

func actionTimes(_ events: [Event]) -> [Double] {
    events.compactMap { event in
        switch event.type {
        case "click", "down", "key", "scroll":
            return event.t
        default:
            return nil
        }
    }.sorted()
}

private func busyIntervals(_ times: [Double]) -> [(Double, Double)] {
    var intervals: [(Double, Double)] = []
    for time in times {
        let start = time - 0.15
        let end = time + 0.55
        if let last = intervals.last, start <= last.1 {
            intervals[intervals.count - 1].1 = max(last.1, end)
        } else {
            intervals.append((start, end))
        }
    }
    return intervals
}

private func timingForRange(_ start: Double, _ end: Double, busy: [(Double, Double)], target: Double?) -> (segments: [Segment], cuts: [Cut]) {
    let rangeStart = min(start, end)
    let rangeEnd = max(start, end)
    if rangeEnd - rangeStart < 0.05 { return ([], []) }
    let clippedBusy = clipIntervals(busy, to: rangeStart, rangeEnd)
    let idle = complement(clippedBusy, within: rangeStart, rangeEnd)
    let busyLength = clippedBusy.reduce(0) { $0 + ($1.1 - $1.0) }
    let idleLength = idle.reduce(0) { $0 + ($1.1 - $1.0) }

    if let target {
        if busyLength >= target - 0.05 {
            var segments: [Segment] = []
            if busyLength > target + 0.05 {
                let speed = min(2, busyLength / max(target, 0.2))
                segments = clippedBusy.map { Segment(start: $0.0, end: $0.1, speed: speed) }
            }
            return (segments, idle.map { Cut(start: $0.0, end: $0.1) })
        }
        let idleOutput = max(0.05, target - busyLength)
        var speed = idleLength / idleOutput
        if idleLength <= 0 { return ([], []) }
        if speed > 8 {
            let keep = idleOutput * 8
            return (idle.map { Segment(start: $0.0, end: $0.1, speed: 8) }, cutsToFit(idle, keep: keep))
        }
        speed = max(1, speed)
        if abs(speed - 1) < 0.05 { return ([], []) }
        return (idle.map { Segment(start: $0.0, end: $0.1, speed: speed) }, [])
    }

    var segments: [Segment] = []
    var cuts: [Cut] = []
    for gap in idle {
        let length = gap.1 - gap.0
        if length > 8 {
            let cutStart = gap.0 + 0.25
            let cutEnd = gap.1 - 0.25
            if cutEnd > cutStart { cuts.append(Cut(start: cutStart, end: cutEnd)) }
        } else if length >= 1.2 {
            segments.append(Segment(start: gap.0, end: gap.1, speed: 4))
        }
    }
    return (segments, cuts)
}

private func cutsToFit(_ idle: [(Double, Double)], keep: Double) -> [Cut] {
    var remaining = idle.reduce(0) { $0 + ($1.1 - $1.0) } - keep
    if remaining <= 0.05 { return [] }
    var cuts: [Cut] = []
    for gap in idle.sorted(by: { ($0.1 - $0.0) > ($1.1 - $1.0) }) {
        if remaining <= 0.05 { break }
        let length = gap.1 - gap.0
        let remove = min(length, remaining)
        let cutStart = gap.0 + (length - remove) / 2
        cuts.append(Cut(start: cutStart, end: cutStart + remove))
        remaining -= remove
    }
    return cuts
}

private func clipIntervals(_ intervals: [(Double, Double)], to start: Double, _ end: Double) -> [(Double, Double)] {
    intervals.compactMap { interval in
        let clippedStart = max(start, interval.0)
        let clippedEnd = min(end, interval.1)
        return clippedEnd - clippedStart > 0.02 ? (clippedStart, clippedEnd) : nil
    }
}

func planCommand(takePath: String) async -> [String: Any] {
    do {
        let take = absolutePath(takePath)
        let raw = take.appendingPathComponent("raw.mov")
        guard FileManager.default.fileExists(atPath: raw.path) else {
            throw ToolError("Missing raw.mov. Record a take first.")
        }
        let duration = try await mediaDuration(raw)
        let eventsURL = take.appendingPathComponent("events.jsonl")
        let events = FileManager.default.fileExists(atPath: eventsURL.path) ? try loadEvents(at: eventsURL) : []
        let editURL = take.appendingPathComponent("edit.json")
        var edit = FileManager.default.fileExists(atPath: editURL.path) ? try loadEdit(at: editURL) : EditList()
        if (edit.sourceSize?.width ?? 0) < 2 {
            let asset = AVURLAsset(url: raw)
            if let track = try? await asset.loadTracks(withMediaType: .video).first,
               let size = try? await track.load(.naturalSize), size.width > 1 {
                edit.sourceSize = size
            }
        }
        let timing = plannedTiming(events: events, duration: duration, steps: edit.steps)
        edit.segments = timing.segments
        edit.cuts = timing.cuts
        edit.blurs.append(contentsOf: secretBlurs(events: events, sourceSize: edit.sourceSize, existing: edit.blurs))
        try writeJSONObject(editObject(from: edit), to: editURL)
        let map = buildTimeMap(duration: duration, segments: edit.segments, cuts: edit.cuts)
        let size = edit.sourceSize ?? .zero
        return [
            "ok": true,
            "take": take.path,
            "edit": editURL.path,
            "sourceDuration": duration,
            "duration": map.outputDuration,
            "segments": edit.segments.count,
            "cuts": edit.cuts.count,
            "steps": stepTimeline(events: events, duration: duration, map: map),
            "warnings": collectWarnings(events: events, edit: edit, duration: duration, sourceSize: size),
        ]
    } catch {
        return ["ok": false, "error": error.localizedDescription]
    }
}

func timelineCommand(takePath: String) async -> [String: Any] {
    do {
        let take = absolutePath(takePath)
        let raw = take.appendingPathComponent("raw.mov")
        guard FileManager.default.fileExists(atPath: raw.path) else {
            throw ToolError("Missing raw.mov. Record a take first.")
        }
        let duration = try await mediaDuration(raw)
        let events = try loadEvents(at: take.appendingPathComponent("events.jsonl"))
        let editURL = take.appendingPathComponent("edit.json")
        let edit = FileManager.default.fileExists(atPath: editURL.path) ? try loadEdit(at: editURL) : EditList()
        let map = buildTimeMap(duration: duration, segments: edit.segments, cuts: edit.cuts)
        let steps = stepTimeline(events: events, duration: duration, map: map)
        let payload: [String: Any] = [
            "ok": true,
            "take": take.path,
            "sourceDuration": duration,
            "duration": map.outputDuration,
            "steps": steps,
            "audio": edit.passthrough["audio"] ?? [],
            "warnings": collectWarnings(events: events, edit: edit, duration: duration, sourceSize: edit.sourceSize ?? .zero),
        ]
        try writeJSONObject(payload, to: take.appendingPathComponent("timeline.json"))
        return payload
    } catch {
        return ["ok": false, "error": error.localizedDescription]
    }
}

func markCommand(takePath: String, label: String, at: Double?) -> [String: Any] {
    let take = absolutePath(takePath)
    let trimmed = label.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty { return ["ok": false, "error": "--label needs the step name"] }
    let session = readJSONObject(at: take.appendingPathComponent("session.json"))
    let recording = (session?["state"] as? String) == "recording"
    if let at {
        let eventsURL = take.appendingPathComponent("events.jsonl")
        let line = markerJSON(label: trimmed, time: at) + "\n"
        if let handle = FileHandle(forWritingAtPath: eventsURL.path) {
            handle.seekToEndOfFile()
            handle.write(Data(line.utf8))
            try? handle.close()
        } else {
            try? line.write(to: eventsURL, atomically: true, encoding: .utf8)
        }
        return ["ok": true, "take": take.path, "label": trimmed, "t": at]
    }
    if !recording {
        return ["ok": false, "error": "Nothing is recording in \(take.path). Pass --at to mark a finished take."]
    }
    let inbox = take.appendingPathComponent("inbox.jsonl")
    let line = markerJSON(label: trimmed, time: 0) + "\n"
    if let handle = FileHandle(forWritingAtPath: inbox.path) {
        handle.seekToEndOfFile()
        handle.write(Data(line.utf8))
        try? handle.close()
    } else {
        try? line.write(to: inbox, atomically: true, encoding: .utf8)
    }
    return ["ok": true, "take": take.path, "label": trimmed, "queued": true]
}

func markerJSON(label: String, time: Double) -> String {
    let escaped = label
        .replacingOccurrences(of: "\\", with: "\\\\")
        .replacingOccurrences(of: "\"", with: "\\\"")
    return String(format: "{\"t\":%.3f,\"type\":\"marker\",\"label\":\"%@\"}", time, escaped)
}

func mediaDuration(_ url: URL) async throws -> Double {
    let asset = AVURLAsset(url: url)
    let duration = try await asset.load(.duration).seconds
    guard duration.isFinite, duration > 0 else {
        throw ToolError("The recording has no duration")
    }
    return duration
}

private func secretBlurs(events: [Event], sourceSize: CGSize?, existing: [BlurRect]) -> [BlurRect] {
    let width = sourceSize?.width ?? 0
    let height = sourceSize?.height ?? 0
    var added: [BlurRect] = []
    for event in events where event.type == "secret" {
        let blur = BlurRect(
            x: event.w == nil ? 0 : event.x,
            y: event.h == nil ? 0 : event.y,
            width: event.w ?? width,
            height: event.h ?? height,
            radius: 24,
            t0: event.t,
            t1: event.t1 ?? event.t + 0.8
        )
        let already = existing.contains { existingBlur in
            abs(existingBlur.x - blur.x) < 1 && abs((existingBlur.t0 ?? -1) - event.t) < 0.05
        }
        if !already && blur.width > 1 && blur.height > 1 {
            added.append(blur)
        }
    }
    return added
}

private func complement(_ busy: [(Double, Double)], within start: Double, _ end: Double) -> [(Double, Double)] {
    var cursor = start
    var gaps: [(Double, Double)] = []
    for interval in busy.sorted(by: { $0.0 < $1.0 }) {
        if interval.0 > cursor + 0.02 { gaps.append((cursor, interval.0)) }
        cursor = max(cursor, interval.1)
    }
    if end > cursor + 0.02 { gaps.append((cursor, end)) }
    return gaps
}
