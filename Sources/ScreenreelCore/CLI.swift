import CoreGraphics
import Foundation

public enum Screenreel {
    public static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.first == "record", arguments.dropFirst().first == "run" {
            do {
                let flags = try parseFlags(Array(arguments.dropFirst(2)))
                guard let take = flags.take else { throw ToolError("--take needs a directory") }
                RecordCommand.run(takePath: take, options: flags.capture)
            } catch {
                log(error.localizedDescription)
                exit(1)
            }
            return
        }

        let done = StopFlag()
        Task.detached {
            let payload = await run(arguments)
            emit(payload)
        }
        while !done.isStopped {
            RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05))
        }
    }
}

private func run(_ arguments: [String]) async -> [String: Any] {
    do {
        guard let command = arguments.first else {
            throw ToolError(usage)
        }
        switch command {
        case "doctor":
            return await doctor()
        case "record":
            let rest = Array(arguments.dropFirst())
            guard let sub = rest.first else { throw ToolError(usage) }
            let flags = try parseFlags(Array(rest.dropFirst()))
            guard let take = flags.take else { throw ToolError("--take needs a directory") }
            switch sub {
            case "start":
                return await RecordCommand.start(takePath: take, options: flags.capture)
            case "stop":
                return await RecordCommand.stop(takePath: take)
            default:
                throw ToolError(usage)
            }
        case "mark":
            let flags = try parseFlags(Array(arguments.dropFirst()))
            guard let take = flags.take else { throw ToolError("--take needs a directory") }
            guard let label = flags.label else { throw ToolError("--label needs the step name") }
            let at = try flags.at.map { try parseTimes($0).first ?? 0 }
            return markCommand(takePath: take, label: label, at: flags.at == nil ? nil : at)
        case "plan":
            let flags = try parseFlags(Array(arguments.dropFirst()))
            guard let take = flags.take else { throw ToolError("--take needs a directory") }
            return await planCommand(takePath: take)
        case "timeline":
            let flags = try parseFlags(Array(arguments.dropFirst()))
            guard let take = flags.take else { throw ToolError("--take needs a directory") }
            return await timelineCommand(takePath: take)
        case "beautify":
            let flags = try parseFlags(Array(arguments.dropFirst()))
            guard let take = flags.take else { throw ToolError("--take needs a directory") }
            return await beautify(takePath: take, preview: flags.preview)
        case "frames":
            let flags = try parseFlags(Array(arguments.dropFirst()))
            guard let take = flags.take else { throw ToolError("--take needs a directory") }
            return await extractFrames(takePath: take, flags: flags)
        case "help", "--help", "-h":
            return ["ok": true, "commands": usage]
        default:
            throw ToolError(usage)
        }
    } catch {
        return ["ok": false, "error": error.localizedDescription]
    }
}

struct Flags {
    var take: String?
    var at: String?
    var label: String?
    var display: String?
    var crop: String?
    var fps: String?
    var every: String?
    var markers = false
    var preview = false

    var capture: CaptureOptions {
        var options = CaptureOptions()
        if let fps, let value = Int(fps), value > 0 { options.fps = value }
        options.display = display
        if let crop { options.crop = parseCrop(crop) }
        return options
    }
}

struct CaptureOptions {
    var fps: Int = 30
    var display: String?
    var crop: CGRect?
}

private func parseCrop(_ text: String) -> CGRect? {
    let parts = text.split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
    guard parts.count == 4 else { return nil }
    return CGRect(x: parts[0], y: parts[1], width: parts[2], height: parts[3])
}

private func parseFlags(_ arguments: [String]) throws -> Flags {
    var flags = Flags()
    var index = 0
    let valued = ["--take", "--at", "--label", "--display", "--crop", "--fps", "--every"]
    while index < arguments.count {
        let argument = arguments[index]
        if argument == "--markers" {
            flags.markers = true
            index += 1
            continue
        }
        if argument == "--preview" {
            flags.preview = true
            index += 1
            continue
        }
        if valued.contains(argument) {
            let valueIndex = index + 1
            guard valueIndex < arguments.count else { throw ToolError("\(argument) needs a value") }
            assign(&flags, argument, arguments[valueIndex])
            index += 2
            continue
        }
        if let name = valued.first(where: { argument.hasPrefix($0 + "=") }) {
            assign(&flags, name, String(argument.dropFirst(name.count + 1)))
            index += 1
            continue
        }
        throw ToolError("Unknown argument \(argument)")
    }
    return flags
}

private func assign(_ flags: inout Flags, _ name: String, _ value: String) {
    switch name {
    case "--take": flags.take = value
    case "--at": flags.at = value
    case "--label": flags.label = value
    case "--display": flags.display = value
    case "--crop": flags.crop = value
    case "--fps": flags.fps = value
    case "--every": flags.every = value
    default: break
    }
}

private let usage = """
screenreel doctor
screenreel record start --take <dir> [--display N] [--crop x,y,w,h] [--fps 30]
screenreel mark --take <dir> --label "step 2: name"
screenreel record stop --take <dir>
screenreel plan --take <dir>
screenreel beautify --take <dir> [--preview]
screenreel frames --take <dir> --at 1.2,4.0
screenreel frames --take <dir> --markers
screenreel frames --take <dir> --every 5
screenreel timeline --take <dir>
"""
