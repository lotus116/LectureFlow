// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "LectureFlow",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "LectureFlow", targets: ["LectureFlow"])],
    targets: [
        .target(name: "LectureFlowCore"),
        .executableTarget(name: "LectureFlow", dependencies: ["LectureFlowCore"]),
        .testTarget(name: "LectureFlowCoreTests", dependencies: ["LectureFlowCore"])
    ]
)
