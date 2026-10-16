import Darwin
import Foundation

struct ToolError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
    init(_ message: String) { self.message = message }
}

func jsonDouble(_ value: Any?) -> Double? {
    if let number = value as? Double { return number }
    if let number = value as? Int { return Double(number) }
    if let number = value as? NSNumber { return number.doubleValue }
    return nil
}

func jsonInt(_ value: Any?) -> Int? {
    if let number = value as? Int { return number }
    if let number = value as? Double { return Int(number) }
    if let number = value as? NSNumber { return number.intValue }
    return nil
}

func readJSONObject(at url: URL) -> [String: Any]? {
    guard let data = try? Data(contentsOf: url) else { return nil }
    return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
}

func writeJSONObject(_ object: [String: Any], to url: URL) throws {
    let data = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
    let temporary = url.appendingPathExtension("tmp")
    try data.write(to: temporary, options: .atomic)
    if FileManager.default.fileExists(atPath: url.path) {
        try FileManager.default.removeItem(at: url)
    }
    try FileManager.default.moveItem(at: temporary, to: url)
}

func emit(_ payload: [String: Any]) -> Never {
    var object = payload
    if object["ok"] == nil { object["ok"] = false }
    let ok = object["ok"] as? Bool ?? false
    if JSONSerialization.isValidJSONObject(object),
       let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    } else {
        let fallback = #"{"ok":false,"error":"Could not encode JSON"}"# + "\n"
        FileHandle.standardOutput.write(Data(fallback.utf8))
    }
    exit(ok ? 0 : 1)
}

func log(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

func absolutePath(_ path: String) -> URL {
    URL(fileURLWithPath: path).standardizedFileURL
}

func currentExecutablePath() -> String {
    var buffer = [CChar](repeating: 0, count: Int(MAXPATHLEN))
    let count = proc_pidpath(getpid(), &buffer, UInt32(buffer.count))
    if count > 0 { return String(cString: buffer) }
    return CommandLine.arguments[0]
}

struct ProcessSnap {
    var pid: Int32
    var name: String
    var path: String

    var json: [String: Any] {
        ["pid": Int(pid), "name": name, "path": path]
    }
}

func processSnapshot(_ pid: Int32) -> ProcessSnap {
    var nameBuffer = [CChar](repeating: 0, count: 256)
    proc_name(pid, &nameBuffer, UInt32(nameBuffer.count))
    var pathBuffer = [CChar](repeating: 0, count: Int(MAXPATHLEN))
    proc_pidpath(pid, &pathBuffer, UInt32(pathBuffer.count))
    let name = String(cString: nameBuffer)
    let path = String(cString: pathBuffer)
    return ProcessSnap(pid: pid, name: name.isEmpty ? "unknown" : name, path: path)
}

func launchingApp() -> ProcessSnap {
    var pid = getppid()
    var last = processSnapshot(pid)
    for _ in 0..<10 {
        let snap = processSnapshot(pid)
        if snap.pid > 0 { last = snap }
        if snap.path.contains(".app/") { return snap }
        var info = proc_bsdinfo()
        let size = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, Int32(MemoryLayout<proc_bsdinfo>.size))
        if size <= 0 { break }
        let parent = Int32(info.pbi_ppid)
        if parent == 0 || parent == pid { break }
        pid = parent
    }
    return last
}

func tailFile(_ url: URL, maxLines: Int = 30) -> String {
    guard let text = try? String(contentsOf: url, encoding: .utf8) else { return "" }
    let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
    return lines.suffix(maxLines).joined(separator: "\n")
}

final class StopFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    func stop() {
        lock.lock()
        value = true
        lock.unlock()
    }

    var isStopped: Bool {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}
