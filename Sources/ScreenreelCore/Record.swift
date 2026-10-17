import AVFoundation
import ApplicationServices
import CoreGraphics
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

private let sessionName = "session.json"
private let rawName = "raw.mov"
private let eventsName = "events.jsonl"
private let editName = "edit.json"
private let logName = "recorder.log"

private var activeRecorder: Recorder?

private let eventTapCallback: CGEventTapCallBack = { _, type, event, _ in
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let tap = activeRecorder?.eventTap {
            CGEvent.tapEnable(tap: tap, enable: true)
        }
        return Unmanaged.passUnretained(event)
    }
    activeRecorder?.handle(type: type, event: event)
    return Unmanaged.passUnretained(event)
}

final class Recorder: NSObject, SCStreamOutput {
    let take: URL
    let stopFlag = StopFlag()
    var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    private var signalSources: [DispatchSourceSignal] = []
    private let videoQueue = DispatchQueue(label: "screenreel.video")
    private let eventLock = NSLock()
    private var stream: SCStream?
    private var writer: AVAssetWriter?
    private var writerInput: AVAssetWriterInput?
    private var adaptor: AVAssetWriterInputPixelBufferAdaptor?
    private var startedSession = false
    private var zeroUptime: Double?
    private var pending: [(uptime: Double, line: String)] = []
    private var eventStream: OutputStream?
    private var lastMoveUptime = 0.0
    private var map = DisplayMap(topLeftX: 0, topLeftY: 0, pointWidth: 1, pointHeight: 1, pixelWidth: 1, pixelHeight: 1)
    private var pixelSize = CGSize.zero
    private var lastPTS = CMTime.zero
    private var basePTS: CMTime?
    var options = CaptureOptions()
    private var cropOrigin = CGPoint.zero
    private var inboxOffset: UInt64 = 0

    init(take: URL) {
        self.take = take
    }

    func run() {
        activeRecorder = self
        installSignals()
        let finished = StopFlag()
        Task.detached { [self] in
            do {
                try await self.startCapture()
                while !self.stopFlag.isStopped {
                    self.drainMarks()
                    try await Task.sleep(nanoseconds: 40_000_000)
                }
                try await self.finishCapture()
                self.writeSession(state: "done", error: nil)
                exitCode = 0
            } catch {
                self.writeSession(state: "failed", error: error.localizedDescription)
                log(error.localizedDescription)
                exitCode = 1
            }
            finished.stop()
        }
        while !finished.isStopped {
            RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.05))
        }
        activeRecorder = nil
        exit(exitCode)
    }

    private var exitCode: Int32 = 1

    func handle(type: CGEventType, event: CGEvent) {
        let uptime = ProcessInfo.processInfo.systemUptime
        let location = event.location
        let pixel = map.pixel(eventX: location.x, eventY: location.y)
        let x = pixel.0 - cropOrigin.x
        let y = pixel.1 - cropOrigin.y
        switch type {
        case .mouseMoved, .leftMouseDragged, .rightMouseDragged, .otherMouseDragged:
            if uptime - lastMoveUptime < 1.0 / 60.0 { return }
            lastMoveUptime = uptime
            enqueue(uptime: uptime, line: moveLine(time: 0, x: x, y: y))
        case .leftMouseDown:
            enqueue(uptime: uptime, line: pointerLine(type: "down", x: x, y: y, button: "left"))
        case .rightMouseDown:
            enqueue(uptime: uptime, line: pointerLine(type: "down", x: x, y: y, button: "right"))
        case .otherMouseDown:
            enqueue(uptime: uptime, line: pointerLine(type: "down", x: x, y: y, button: "other"))
        case .leftMouseUp:
            enqueue(uptime: uptime, line: pointerLine(type: "up", x: x, y: y, button: "left"))
        case .rightMouseUp:
            enqueue(uptime: uptime, line: pointerLine(type: "up", x: x, y: y, button: "right"))
        case .scrollWheel:
            if uptime - lastMoveUptime < 1.0 / 60.0 { return }
            lastMoveUptime = uptime
            let dx = event.getDoubleValueField(.scrollWheelEventDeltaAxis2)
            let dy = event.getDoubleValueField(.scrollWheelEventDeltaAxis1)
            enqueue(uptime: uptime, line: String(format: "{\"t\":0,\"type\":\"scroll\",\"x\":%.1f,\"y\":%.1f,\"dx\":%.2f,\"dy\":%.2f}", x, y, dx, dy))
        case .keyDown:
            if event.getIntegerValueField(.keyboardEventAutorepeat) == 1 { return }
            enqueue(uptime: uptime, line: #"{"t":0,"type":"key"}"#)
        default:
            break
        }
    }

    private func startCapture() async throws {
        guard AXIsProcessTrusted() else {
            throw ToolError("Accessibility is off. Run screenreel doctor and allow the app that launches screenreel.")
        }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let mainID = CGMainDisplayID()
        let display: SCDisplay
        if let wanted = options.display {
            guard let match = content.displays.first(where: { String($0.displayID) == wanted }) else {
                throw ToolError("No display matches \(wanted). Run screenreel doctor to list displays.")
            }
            display = match
        } else if let main = content.displays.first(where: { $0.displayID == mainID }) ?? content.displays.first {
            display = main
        } else {
            throw ToolError("No display is available to record.")
        }
        let mode = CGDisplayCopyDisplayMode(display.displayID)
        var pixelWidth = even(Int(mode?.pixelWidth ?? display.width))
        var pixelHeight = even(Int(mode?.pixelHeight ?? display.height))
        let bounds = CGDisplayBounds(display.displayID)
        let mainBounds = CGDisplayBounds(mainID)
        let topLeft = displayTopLeft(displayBounds: bounds, mainBounds: mainBounds)
        map = DisplayMap(
            topLeftX: topLeft.x,
            topLeftY: topLeft.y,
            pointWidth: bounds.width,
            pointHeight: bounds.height,
            pixelWidth: Double(pixelWidth),
            pixelHeight: Double(pixelHeight)
        )
        if let crop = options.crop {
            cropOrigin = crop.origin
            pixelWidth = even(Int(crop.width))
            pixelHeight = even(Int(crop.height))
        }
        pixelSize = CGSize(width: pixelWidth, height: pixelHeight)

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        if options.crop != nil {
            configuration.sourceRect = CGRect(x: cropOrigin.x, y: cropOrigin.y, width: CGFloat(pixelWidth), height: CGFloat(pixelHeight))
        }
        configuration.width = pixelWidth
        configuration.height = pixelHeight
        configuration.pixelFormat = kCVPixelFormatType_32BGRA
        configuration.showsCursor = false
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(max(1, options.fps)))
        configuration.queueDepth = 6
        configuration.colorSpaceName = CGColorSpace.sRGB

        let stream = SCStream(filter: filter, configuration: configuration, delegate: nil)
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: videoQueue)
        self.stream = stream
        try openEvents()
        try installEventTap()
        try await stream.startCapture()
        writeSession(state: "recording", error: nil)
        log("recording \(pixelWidth)x\(pixelHeight)")
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, stopFlag.isStopped == false else { return }
        guard frameIsUsable(sampleBuffer), let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        if writer == nil {
            do {
                try prepareWriter(width: CVPixelBufferGetWidth(buffer), height: CVPixelBufferGetHeight(buffer))
            } catch {
                log(error.localizedDescription)
                stopFlag.stop()
                return
            }
        }
        guard let writer, let writerInput, let adaptor else { return }
        if !startedSession {
            writer.startSession(atSourceTime: .zero)
            startedSession = true
            markVideoZero()
        }
        if basePTS == nil { basePTS = pts }
        let relative = CMTimeSubtract(pts, basePTS ?? pts)
        guard writerInput.isReadyForMoreMediaData else { return }
        if adaptor.append(buffer, withPresentationTime: relative) {
            lastPTS = relative
        }
    }

    private func finishCapture() async throws {
        if let stream {
            try await stream.stopCapture()
        }
        if let tap = eventTap {
            CGEvent.tapEnable(tap: tap, enable: false)
        }
        videoQueue.sync {}
        drainMarks()
        writerInput?.markAsFinished()
        if let writer {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                writer.finishWriting { continuation.resume() }
            }
            if writer.status != .completed {
                throw ToolError(writer.error?.localizedDescription ?? "Could not finish the recording")
            }
        }
        flushEvents()
        eventStream?.close()
    }

    private func prepareWriter(width: Int, height: Int) throws {
        let url = take.appendingPathComponent(rawName)
        try? FileManager.default.removeItem(at: url)
        let writer = try AVAssetWriter(outputURL: url, fileType: .mov)
        let settings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: width * height * 4,
                AVVideoExpectedSourceFrameRateKey: 30,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
            ],
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        input.expectsMediaDataInRealTime = true
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: nil)
        guard writer.canAdd(input) else { throw ToolError("Could not prepare the recording file") }
        writer.add(input)
        guard writer.startWriting() else {
            throw ToolError(writer.error?.localizedDescription ?? "Could not start writing the recording")
        }
        self.writer = writer
        self.writerInput = input
        self.adaptor = adaptor
    }

    private func installEventTap() throws {
        let mask = bit(.mouseMoved)
            | bit(.leftMouseDragged)
            | bit(.rightMouseDragged)
            | bit(.otherMouseDragged)
            | bit(.leftMouseDown)
            | bit(.rightMouseDown)
            | bit(.otherMouseDown)
            | bit(.leftMouseUp)
            | bit(.rightMouseUp)
            | bit(.scrollWheel)
            | bit(.keyDown)
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .listenOnly,
            eventsOfInterest: mask,
            callback: eventTapCallback,
            userInfo: nil
        ) else {
            throw ToolError("Accessibility is off. Run screenreel doctor and allow the app that launches screenreel.")
        }
        eventTap = tap
        let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        runLoopSource = source
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
    }

    private func installSignals() {
        signal(SIGTERM, SIG_IGN)
        signal(SIGINT, SIG_IGN)
        for code in [SIGTERM, SIGINT] {
            let source = DispatchSource.makeSignalSource(signal: code, queue: .main)
            source.setEventHandler { [weak self] in
                self?.stopFlag.stop()
            }
            source.resume()
            signalSources.append(source)
        }
    }

    private func openEvents() throws {
        let url = take.appendingPathComponent(eventsName)
        FileManager.default.createFile(atPath: url.path, contents: nil)
        guard let stream = OutputStream(url: url, append: false) else {
            throw ToolError("Could not open the event log")
        }
        stream.open()
        eventStream = stream
    }

    private func enqueue(uptime: Double, line: String) {
        eventLock.lock()
        defer { eventLock.unlock() }
        if let zero = zeroUptime {
            writeLocked(line: stamp(line, time: max(0, uptime - zero)))
        } else {
            pending.append((uptime, line))
        }
    }

    private func markVideoZero() {
        eventLock.lock()
        defer { eventLock.unlock() }
        let zero = ProcessInfo.processInfo.systemUptime
        zeroUptime = zero
        for item in pending {
            writeLocked(line: stamp(item.line, time: max(0, item.uptime - zero)))
        }
        pending.removeAll()
        let width = Int(pixelSize.width)
        let height = Int(pixelSize.height)
        writeLocked(line: "{\"t\":0,\"type\":\"geometry\",\"x\":0,\"y\":0,\"w\":\(width),\"h\":\(height)}")
    }

    private func flushEvents() {
        eventLock.lock()
        defer { eventLock.unlock() }
        if zeroUptime == nil {
            zeroUptime = ProcessInfo.processInfo.systemUptime
            for item in pending {
                writeLocked(line: stamp(item.line, time: 0))
            }
            pending.removeAll()
        }
    }

    private func writeLocked(line: String) {
        guard let data = (line + "\n").data(using: .utf8) else { return }
        data.withUnsafeBytes { buffer in
            guard let base = buffer.bindMemory(to: UInt8.self).baseAddress else { return }
            eventStream?.write(base, maxLength: buffer.count)
        }
    }

    private func drainMarks() {
        let url = take.appendingPathComponent("inbox.jsonl")
        guard let handle = FileHandle(forReadingAtPath: url.path) else { return }
        defer { try? handle.close() }
        do {
            try handle.seek(toOffset: inboxOffset)
        } catch {
            return
        }
        let data = handle.readDataToEndOfFile()
        guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
        let newline = text.last == "\n" ? text : String(text.prefix(upTo: text.lastIndex(of: "\n") ?? text.endIndex))
        if text.last != "\n", text.lastIndex(of: "\n") == nil { return }
        let consumed = newline.utf8.count
        inboxOffset += UInt64(consumed)
        let drainedAt = ProcessInfo.processInfo.systemUptime
        for raw in newline.split(separator: "\n") where raw.contains("\"label\"") {
            guard let object = (try? JSONSerialization.jsonObject(with: Data(raw.utf8))) as? [String: Any],
                  let label = object["label"] as? String else { continue }
            let stamped = jsonDouble(object["mono"])
            let legacy = jsonDouble(object["t"]) ?? 0
            let called = stamped ?? (legacy > 1 ? legacy : drainedAt)
            enqueue(uptime: called, line: markerJSON(label: label, time: 0))
        }
    }

    private func writeSession(state: String, error: String?) {
        var object: [String: Any] = [
            "pid": Int(ProcessInfo.processInfo.processIdentifier),
            "state": state,
            "take": take.path,
            "raw": take.appendingPathComponent(rawName).path,
            "events": take.appendingPathComponent(eventsName).path,
            "width": Int(pixelSize.width),
            "height": Int(pixelSize.height),
            "duration": lastPTS.seconds.isFinite ? lastPTS.seconds : 0,
        ]
        if let error { object["error"] = error }
        try? writeJSONObject(object, to: take.appendingPathComponent(sessionName))
    }
}

private func bit(_ type: CGEventType) -> CGEventMask {
    CGEventMask(1) << CGEventMask(type.rawValue)
}

private func even(_ value: Int) -> Int {
    value - (value % 2)
}

private func moveLine(time: Double, x: Double, y: Double) -> String {
    String(format: #"{"t":%.3f,"type":"move","x":%.1f,"y":%.1f}"#, time, x, y)
}

private func pointerLine(type: String, x: Double, y: Double, button: String) -> String {
    String(format: "{\"t\":0,\"type\":\"%@\",\"x\":%.1f,\"y\":%.1f,\"button\":\"%@\"}", type, x, y, button)
}

private func stamp(_ line: String, time: Double) -> String {
    guard let range = line.range(of: "\"t\":") else { return line }
    let after = line[range.upperBound...]
    let end = after.firstIndex(where: { $0 == "," || $0 == "}" }) ?? after.endIndex
    var copy = line
    copy.replaceSubrange(range.upperBound..<end, with: String(format: "%.3f", time))
    return String(copy)
}

private func frameIsUsable(_ sample: CMSampleBuffer) -> Bool {
    guard CMSampleBufferGetImageBuffer(sample) != nil else { return false }
    guard let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[String: Any]],
          let raw = attachments.first?[SCStreamFrameInfo.status.rawValue] as? Int,
          let status = SCFrameStatus(rawValue: raw) else {
        return true
    }
    return status == .complete
}

enum RecordCommand {
    static func start(takePath: String, options: CaptureOptions) async -> [String: Any] {
        let take = absolutePath(takePath)
        do {
            try FileManager.default.createDirectory(at: take, withIntermediateDirectories: true)
        } catch {
            return ["ok": false, "error": "Could not create \(take.path)"]
        }
        if let existing = readJSONObject(at: take.appendingPathComponent(sessionName)),
           let pid = jsonInt(existing["pid"]),
           let state = existing["state"] as? String,
           state == "recording",
           kill(pid_t(pid), 0) == 0 {
            return ["ok": false, "error": "Already recording in \(take.path)", "pid": pid]
        }
        for name in [rawName, eventsName, editName, "out.mp4", "preview.mp4", "timeline.json", "inbox.jsonl", sessionName, logName] {
            try? FileManager.default.removeItem(at: take.appendingPathComponent(name))
        }
        try? FileManager.default.removeItem(at: take.appendingPathComponent("frames"))

        let executable = currentExecutablePath()
        let logURL = take.appendingPathComponent(logName)
        let pid: pid_t
        do {
            var childArguments = ["record", "run", "--take", take.path, "--fps", String(options.fps)]
            if let display = options.display { childArguments.append(contentsOf: ["--display", display]) }
            if let crop = options.crop {
                childArguments.append(contentsOf: ["--crop", "\(Int(crop.origin.x)),\(Int(crop.origin.y)),\(Int(crop.width)),\(Int(crop.height))"])
            }
            pid = try spawnDetached(
                executable: executable,
                arguments: childArguments,
                stdout: logURL.path,
                stderr: logURL.path
            )
        } catch {
            return ["ok": false, "error": error.localizedDescription]
        }

        let sessionURL = take.appendingPathComponent(sessionName)
        let deadline = Date().addingTimeInterval(20)
        var session = readJSONObject(at: sessionURL)
        while Date() < deadline {
            session = readJSONObject(at: sessionURL)
            if let state = session?["state"] as? String, state == "recording" || state == "failed" {
                break
            }
            if kill(pid, 0) != 0 { break }
            try? await Task.sleep(nanoseconds: 40_000_000)
        }
        let state = session?["state"] as? String
        if state != "recording" {
            if kill(pid, 0) == 0 { kill(pid, SIGTERM) }
            let reason = session?["error"] as? String ?? tailFile(logURL)
            let message = reason.isEmpty ? "Recording did not start. Run screenreel doctor." : reason
            return ["ok": false, "error": message, "log": logURL.path]
        }
        return [
            "ok": true,
            "state": "recording",
            "pid": Int(pid),
            "take": take.path,
            "raw": take.appendingPathComponent(rawName).path,
            "events": take.appendingPathComponent(eventsName).path,
            "edit": take.appendingPathComponent(editName).path,
            "width": jsonInt(session?["width"]) ?? 0,
            "height": jsonInt(session?["height"]) ?? 0,
        ]
    }

    static func stop(takePath: String) async -> [String: Any] {
        let take = absolutePath(takePath)
        let sessionURL = take.appendingPathComponent(sessionName)
        guard let session = readJSONObject(at: sessionURL) else {
            return ["ok": false, "error": "No recording found in \(take.path)"]
        }
        if let pid = jsonInt(session["pid"]), kill(pid_t(pid), 0) == 0 {
            kill(pid_t(pid), SIGTERM)
            let deadline = Date().addingTimeInterval(15)
            while Date() < deadline {
                if kill(pid_t(pid), 0) != 0 { break }
                if readJSONObject(at: sessionURL)?["state"] as? String == "done" { break }
                try? await Task.sleep(nanoseconds: 40_000_000)
            }
            if kill(pid_t(pid), 0) == 0 {
                kill(pid_t(pid), SIGKILL)
            }
        }
        let finalSession = readJSONObject(at: sessionURL) ?? session
        if finalSession["state"] as? String == "failed" {
            let message = finalSession["error"] as? String ?? "Recording failed"
            return ["ok": false, "error": message, "log": take.appendingPathComponent(logName).path]
        }
        let raw = take.appendingPathComponent(rawName)
        let eventsURL = take.appendingPathComponent(eventsName)
        guard FileManager.default.fileExists(atPath: raw.path) else {
            return ["ok": false, "error": "Recording file is missing. See \(take.appendingPathComponent(logName).path)"]
        }
        if !FileManager.default.fileExists(atPath: eventsURL.path) {
            FileManager.default.createFile(atPath: eventsURL.path, contents: nil)
        }
        let editURL = take.appendingPathComponent(editName)
        let events = (try? loadEvents(at: eventsURL)) ?? []
        var width = jsonInt(finalSession["width"]) ?? 0
        var height = jsonInt(finalSession["height"]) ?? 0
        if width < 2 || height < 2 {
            let asset = AVURLAsset(url: raw)
            if let track = try? await asset.loadTracks(withMediaType: .video).first,
               let size = try? await track.load(.naturalSize) {
                width = Int(size.width.rounded())
                height = Int(size.height.rounded())
            }
        }
        var edit = EditList()
        edit.sourceSize = CGSize(width: width, height: height)
        if !FileManager.default.fileExists(atPath: editURL.path) {
            do {
                try writeJSONObject(editObject(from: edit), to: editURL)
            } catch {
                return ["ok": false, "error": "Could not write \(editURL.path)"]
            }
        }
        let zooms = autoZooms(events: events, style: edit.style)
        let clicks = events.filter { $0.type == "click" }.count
        return [
            "ok": true,
            "state": "stopped",
            "take": take.path,
            "raw": raw.path,
            "events": eventsURL.path,
            "edit": editURL.path,
            "duration": jsonDouble(finalSession["duration"]) ?? 0,
            "eventCount": events.count,
            "clicks": clicks,
            "sourceSize": ["width": width, "height": height],
            "zoomCount": zooms.count,
            "previewZooms": zooms.prefix(50).map { zoomObject($0) },
        ]
    }

    static func run(takePath: String, options: CaptureOptions) {
        let take = absolutePath(takePath)
        try? FileManager.default.createDirectory(at: take, withIntermediateDirectories: true)
        let recorder = Recorder(take: take)
        recorder.options = options
        recorder.run()
    }
}

private func spawnDetached(executable: String, arguments: [String], stdout: String, stderr: String) throws -> pid_t {
    var pid: pid_t = 0
    var attributes: posix_spawnattr_t?
    guard posix_spawnattr_init(&attributes) == 0 else {
        throw ToolError("Could not start the recorder")
    }
    defer { posix_spawnattr_destroy(&attributes) }
    posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETSID))

    var actions: posix_spawn_file_actions_t?
    guard posix_spawn_file_actions_init(&actions) == 0 else {
        throw ToolError("Could not start the recorder")
    }
    defer { posix_spawn_file_actions_destroy(&actions) }

    let result: Int32 = stdout.withCString { outPath in
        stderr.withCString { errPath in
            "/dev/null".withCString { nullPath in
                var argv: [UnsafeMutablePointer<CChar>?] = [strdup(executable)]
                for argument in arguments {
                    argv.append(strdup(argument))
                }
                argv.append(nil)
                defer {
                    for pointer in argv { free(pointer) }
                }
                posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, nullPath, O_RDONLY, 0)
                posix_spawn_file_actions_addopen(&actions, STDOUT_FILENO, outPath, O_WRONLY | O_CREAT | O_TRUNC, mode_t(0o644))
                if strcmp(outPath, errPath) == 0 {
                    posix_spawn_file_actions_adddup2(&actions, STDOUT_FILENO, STDERR_FILENO)
                } else {
                    posix_spawn_file_actions_addopen(&actions, STDERR_FILENO, errPath, O_WRONLY | O_CREAT | O_TRUNC, mode_t(0o644))
                }
                return posix_spawn(&pid, executable, &actions, &attributes, &argv, environ)
            }
        }
    }
    if result != 0 {
        throw ToolError("Could not start the recorder (\(String(cString: strerror(result))))")
    }
    return pid
}
