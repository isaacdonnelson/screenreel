// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "screenreel",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "screenreel", targets: ["screenreel"])
    ],
    targets: [
        .target(
            name: "ScreenreelCore",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .executableTarget(
            name: "screenreel",
            dependencies: ["ScreenreelCore"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .executableTarget(
            name: "screenreel-check",
            dependencies: ["ScreenreelCore"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
