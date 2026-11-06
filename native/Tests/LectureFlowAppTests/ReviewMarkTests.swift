import AVFoundation
import Foundation
import LectureFlowCore
import XCTest
@testable import LectureFlow

final class ReviewMarkTests: XCTestCase {
    private func caption(_ id: String, start: Double, end: Double,
                         session: String = "capture", revision: Int = 1) -> LiveCaption {
        LiveCaption(id: id, start: start, end: end, english: "Synthetic sentence.",
                    chinese: "合成字幕。", status: "stable", revision: revision, sessionID: session)
    }

    func testBookmarkFollowsSourceAcrossParagraphMergeAndRevision() {
        let mark = ReviewMark(id: "mark", lecture_id: "lecture", capture_id: "capture",
                              capture_time: 6, caption_id: "fragment-b", reviewed: 0,
                              removed: 0, created_at: "2026-11-05T00:00:00Z")
        let original = [caption("fragment-a", start: 0, end: 5),
                        caption("fragment-b", start: 5, end: 10)]
        XCTAssertEqual(mark.caption(in: original)?.id, "fragment-b")
        let merged = [caption("fragment-a", start: 0, end: 10, revision: 9)]
        XCTAssertEqual(mark.caption(in: merged)?.id, "fragment-a")
        XCTAssertNil(mark.caption(in: [caption("fragment-b", start: 5, end: 10, session: "other")]))
    }

    @MainActor func testRowAndCurrentToggleShareDurableStateEvenWhenViewingRemoved() async throws {
        var saved: [[String: Any]] = []
        let store = ReviewMarks(call: { args in
            if args[1] == "add" {
                saved.append(["id": args[6], "lecture_id": "lecture", "capture_id": "capture",
                              "capture_time": 5.0, "caption_id": "current", "reviewed": 0,
                              "removed": 0, "created_at": "2026-11-05T00:00:00Z"])
            } else if args[1] == "remove", let index = saved.firstIndex(where: { $0["id"] as? String == args[3] }) {
                saved[index]["removed"] = 1
            }
            let active = saved.filter { $0["removed"] as? Int == 0 }
            if args[1] == "list" {
                let listed = args.contains("--removed") ? saved.filter { $0["removed"] as? Int == 1 } : active
                return try JSONSerialization.data(withJSONObject: [
                    "marks": listed, "active_marks": active, "active_count": active.count])
            }
            if args[1] == "detail", let mark = saved.first(where: { $0["id"] as? String == args[3] }) {
                return try JSONSerialization.data(withJSONObject: ["mark": mark, "captions": [],
                    "audio_message": "", "target_caption_id": NSNull(), "audio": NSNull()])
            }
            return Data("{}".utf8)
        })
        let current = caption("current", start: 5, end: 10)
        store.selectLecture("lecture")
        await store.refresh()
        XCTAssertTrue(store.matching(current, in: [current]).isEmpty)
        // Both toolbar and row invoke the same operation with the same source caption.
        store.toggle(current, in: [current], lecture: "lecture")
        await store.finishWrites()
        XCTAssertEqual(store.matching(current, in: [current]).count, 1)
        store.showingRemoved = true
        await store.refresh()
        XCTAssertTrue(store.marks.isEmpty)
        XCTAssertEqual(store.matching(current, in: [current]).count, 1)
        store.toggle(current, in: [current], lecture: "lecture")
        await store.finishWrites()
        XCTAssertTrue(store.matching(current, in: [current]).isEmpty)
        XCTAssertEqual(store.marks.count, 1) // Cancellation remains recoverable.
        XCTAssertEqual(saved.count, 1) // No duplicate mark from a second entry point.
    }

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
        XCTAssertTrue(store.activeMarks.isEmpty) // Never show a saved icon after a failed write.
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

extension ReviewMarkTests {
    @MainActor func testBoundedReplayStopsOnSecondAudioChunk() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let format = try XCTUnwrap(AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1))
        let buffer = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 64000))
        buffer.frameLength = 64000
        buffer.floatChannelData?[0].initialize(repeating: 0, count: 64000)
        var files: [String] = []
        for name in ["a", "b"] {
            let url = directory.appendingPathComponent(name + ".caf")
            let file = try AVAudioFile(forWriting: url, settings: format.settings)
            try file.write(from: buffer)
            files.append(url.path)
        }
        let playback = AudioPlayback()
        defer { playback.stop() }
        let audio = ReviewAudio(segment_id: "test", files: files, offset: 3.85,
                                position: 4, last_end: 0.20, pre_roll_seconds: 0.15)
        playback.playReview(audio, markID: "late")
        XCTAssertEqual(playback.reviewID, "late")
        // Playing the entire last file would still be active after this deadline.
        for _ in 0..<60 where playback.reviewID != nil {
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        XCTAssertNil(playback.error)
        XCTAssertNil(playback.reviewID)
    }
}
