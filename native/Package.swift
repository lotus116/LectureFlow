// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "LectureFlow",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "LectureFlow", targets: ["LectureFlow"])],
    targets: [.executableTarget(name: "LectureFlow")]
)
