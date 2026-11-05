import Foundation
import XCTest
@testable import LectureFlow

final class ReviewMarkTests: XCTestCase {
    @MainActor func testFailedSaveRetriesExactAnchorAndBlocksCleanExit() async {
        var calls: [[String]] = []
        var fail = true
        let store = ReviewMarks(call: { arguments in
            calls.append(arguments)
            if fail { throw CocoaError(.fileWriteOutOfSpace) }
            return Data("{}".utf8)
        })
        store.add(lecture: "lecture", capture: "capture", time: 103.25)
        XCTAssertTrue(store.hasUnsavedChanges)
        await store.finishWrites()
        XCTAssertTrue(store.hasUnsavedChanges)
        XCTAssertNotNil(store.error)
        fail = false
        store.retryFailed()
        await store.finishWrites()
        XCTAssertFalse(store.hasUnsavedChanges)
        XCTAssertEqual(calls.count, 2)
        XCTAssertEqual(calls[0], calls[1])
        XCTAssertEqual(calls[1][4], "103.25")
    }

    @MainActor func testExitWaitsForDurableWriteResponse() async {
        var release: CheckedContinuation<Data, Error>?
        let started = expectation(description: "write started")
        let store = ReviewMarks(call: { _ in
            try await withCheckedThrowingContinuation { continuation in
                release = continuation; started.fulfill()
            }
        })
        store.add(lecture: "lecture", capture: "capture", time: 6)
        await fulfillment(of: [started], timeout: 2)
        var completed = false
        let drain = Task { await store.finishWrites(); completed = true }
        await Task.yield()
        XCTAssertFalse(completed)
        XCTAssertTrue(store.saving)
        release?.resume(returning: Data("{}".utf8))
        await drain.value
        XCTAssertTrue(completed)
        XCTAssertFalse(store.hasUnsavedChanges)
    }

    @MainActor func testOldLectureListCannotReplaceNewSelection() async {
        var release: CheckedContinuation<Data, Error>?
        let started = expectation(description: "old lecture loading")
        let store = ReviewMarks(call: { arguments in
            if arguments[2] == "old" {
                return try await withCheckedThrowingContinuation { continuation in
                    release = continuation; started.fulfill()
                }
            }
            return Data("{\"marks\":[]}".utf8)
        })
        store.selectLecture("old")
        await fulfillment(of: [started], timeout: 2)
        store.selectLecture("new")
        release?.resume(returning: Data("""
            {"marks":[{"id":"old-mark","lecture_id":"old","capture_id":"capture",
            "capture_time":4,"reviewed":0,"created_at":"2026-11-05T00:00:00Z"}]}
            """.utf8))
        await store.refresh()
        await Task.yield()
        XCTAssertTrue(store.marks.isEmpty)
        XCTAssertNil(store.selectedID)
        XCTAssertNil(store.detail)
    }
}
