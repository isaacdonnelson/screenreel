import Foundation
import ScreenreelCore

do {
    let failures = try runScreenreelChecks()
    if failures > 0 {
        FileHandle.standardError.write(Data("\(failures) failed\n".utf8))
        exit(1)
    }
} catch {
    FileHandle.standardError.write(Data("FAIL \(error.localizedDescription)\n".utf8))
    exit(1)
}
print("ok")
