import Foundation
import XCTest
import LectureFlowCore
@testable import LectureFlow

final class LivePipeExitTests: XCTestCase {
    func testStoppedMessageMustWaitForActualChildExit() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let child = Process()
        child.executableURL = URL(fileURLWithPath: "/bin/sh")
        child.arguments = ["-c", "read line; echo '{\"type\":\"stopped\",\"translation_pending\":0}'; while [ ! -f \"$1\" ]; do sleep 0.05; done", "test", root.appendingPathComponent("release").path]
        let state = ExitReadiness()
        let ticket = state.begin(.captions)
        let stopped = expectation(description: "backend reports stopped")
        let closed = expectation(description: "reader finishes and child exits")
        let pipe = try LivePipe(process: child, diagnosticURL: root.appendingPathComponent("health.json"),
                               sessionID: "test", model: "unused", completion: { success in
            state.finish(ticket, failure: success ? nil : "failed")
        }) { event in
            if event["type"] as? String == "stopped" { stopped.fulfill() }
            if event["type"] as? String == "closed" { closed.fulfill() }
        }
        pipe.stop()
        wait(for: [stopped], timeout: 5)
        XCTAssertTrue(child.isRunning)
        XCTAssertFalse(state.snapshot.canExit)
        try Data().write(to: root.appendingPathComponent("release"))
        wait(for: [closed], timeout: 5)
        XCTAssertTrue(state.snapshot.canExit)
    }

    func testAbnormalExitAndEOFWithoutStoppedNeverAuthorizeQuit() throws {
        for script in ["exit 0", "echo '{\"type\":\"stopped\"}'; exit 1",
                       "echo '{\"type\":\"error\"}'; echo '{\"type\":\"stopped\"}'",
                       "echo '{\"type\":\"stopped\",\"translation_pending\":1}'"] {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
            defer { try? FileManager.default.removeItem(at: root) }
            let child = Process(); child.executableURL = URL(fileURLWithPath: "/bin/sh")
            child.arguments = ["-c", script]
            let state = ExitReadiness(); let ticket = state.begin(.captions)
            let done = expectation(description: "failed child is accounted for")
            let pipe = try LivePipe(process: child, diagnosticURL: root.appendingPathComponent("health.json"),
                                   sessionID: "test", model: "unused", completion: { success in
                state.finish(ticket, failure: success ? nil : "incomplete")
                done.fulfill()
            }, event: { _ in })
            withExtendedLifetime(pipe) { wait(for: [done], timeout: 5) }
            XCTAssertTrue(state.snapshot.settled)
            XCTAssertFalse(state.snapshot.canExit)
            XCTAssertEqual(state.snapshot.failures, ["incomplete"])
        }
    }
}
