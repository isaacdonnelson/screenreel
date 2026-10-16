import ApplicationServices
import CoreGraphics
import Foundation
import ScreenCaptureKit

func doctor() async -> [String: Any] {
    let screen = CGPreflightScreenCaptureAccess()
    if !screen {
        _ = CGRequestScreenCaptureAccess()
    }
    let screenGranted = CGPreflightScreenCaptureAccess()
    let accessibility = AXIsProcessTrustedWithOptions([
        kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true,
    ] as CFDictionary)
    let parent = processSnapshot(getppid())
    let app = launchingApp()
    var displays: [[String: Any]] = []
    var displayError: String?
    if screenGranted {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            displays = content.displays.map { display in
                let mode = CGDisplayCopyDisplayMode(display.displayID)
                return [
                    "id": Int(display.displayID),
                    "pixels": [Int(mode?.pixelWidth ?? display.width), Int(mode?.pixelHeight ?? display.height)],
                    "points": [display.width, display.height],
                ]
            }
        } catch {
            displayError = error.localizedDescription
        }
    }
    let ready = screenGranted && accessibility && !displays.isEmpty
    let permissionNote = "Permissions stick to \(app.name), not to every shell. The parent process is \(parent.name). An agent shell is a different app from Terminal, so allow that app too if recording is started from it."
    var message = "Ready to record. \(permissionNote)"
    if !screenGranted {
        message = "Screen Recording is off for \(app.name). Turn it on in System Settings, Privacy & Security, Screen Recording, then run doctor again. \(permissionNote)"
    } else if !accessibility {
        message = "Accessibility is off for \(app.name). Turn it on in System Settings, Privacy & Security, Accessibility, so clicks and cursor motion can be logged. Key characters are not recorded. \(permissionNote)"
    } else if displays.isEmpty {
        message = displayError ?? "Screen Recording is allowed, but no display could be opened."
    }
    var payload: [String: Any] = [
        "ok": ready,
        "screenRecording": screenGranted,
        "accessibility": accessibility,
        "executable": currentExecutablePath(),
        "parent": parent.json,
        "permissionApp": app.json,
        "displays": displays,
        "message": message,
    ]
    if let displayError, displays.isEmpty {
        payload["error"] = displayError
    }
    return payload
}
