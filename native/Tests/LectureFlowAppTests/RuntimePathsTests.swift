import Foundation
import XCTest
@testable import LectureFlow

final class RuntimePathsTests: XCTestCase {
    func testParallelInterfaceBundleDoesNotDiscoverExistingCheckout() throws {
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? fm.removeItem(at: root) }
        let app = root.appendingPathComponent("dist/Standalone.app")
        let resources = app.appendingPathComponent("Contents/Resources")
        let python = resources.appendingPathComponent("runtime/bin/python3.12")
        try fm.createDirectory(at: python.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: python)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: python.path)
        for file in ["src/lectureflow/library.py", "src/lectureflow/models.py", "data/library/Library.sqlite3"] {
            let url = root.appendingPathComponent(file)
            try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data().write(to: url)
        }
        let info: [String: Any] = ["CFBundleIdentifier": "local.lectureflow.interface-test",
                                  "CFBundleExecutable": "LectureFlow", "CFBundlePackageType": "APPL",
                                  "LectureFlowIsolatedStorage": true]
        try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
            .write(to: app.appendingPathComponent("Contents/Info.plist"))
        let defaults = UserDefaults.standard
        let previous = defaults.object(forKey: "libraryPath")
        defaults.removeObject(forKey: "libraryPath")
        defer {
            if let previous { defaults.set(previous, forKey: "libraryPath") }
            else { defaults.removeObject(forKey: "libraryPath") }
        }
        let paths = RuntimePaths(bundle: try XCTUnwrap(Bundle(url: app)))
        if ProcessInfo.processInfo.environment["LECTUREFLOW_STORAGE_ROOT"] == nil {
            XCTAssertEqual(paths.storage.lastPathComponent, "LectureFlow Interface")
        }
        if ProcessInfo.processInfo.environment["LECTUREFLOW_LIBRARY_ROOT"] == nil {
            XCTAssertEqual(paths.library, paths.storage.appendingPathComponent("Library"))
        }
        let secondApp = root.appendingPathComponent("dist/Independent.app")
        try fm.copyItem(at: app, to: secondApp)
        var secondInfo = info
        secondInfo["CFBundleIdentifier"] = "local.lectureflow.independent-qa"
        try PropertyListSerialization.data(fromPropertyList: secondInfo, format: .xml, options: 0)
            .write(to: secondApp.appendingPathComponent("Contents/Info.plist"))
        let second = RuntimePaths(bundle: try XCTUnwrap(Bundle(url: secondApp)))
        if ProcessInfo.processInfo.environment["LECTUREFLOW_STORAGE_ROOT"] == nil,
           ProcessInfo.processInfo.environment["LECTUREFLOW_LIBRARY_ROOT"] == nil {
            XCTAssertNotEqual(second.storage, paths.storage)
            XCTAssertNotEqual(second.library, paths.library)
            XCTAssertNotEqual(second.models, paths.models)
        }
        XCTAssertNil(paths.previousModels)
        XCTAssertNil(defaults.object(forKey: "libraryPath"), "Must not adopt the synthetic old library")
        XCTAssertEqual(paths.python.standardizedFileURL, python.standardizedFileURL)
    }
}
