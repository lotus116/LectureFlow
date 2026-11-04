import AVFoundation
import XCTest
@testable import LectureFlowCore

final class ExitReadinessTests: XCTestCase {
    func testQuitAfterPauseSealsAudioButWaitsForDelayedDatabaseRegistration() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let state = ExitReadiness()
        let pump = CapturePump(audioRoot: root, referenceTime: 0)
        let format = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!
        let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4000)!
        pcm.frameLength = 4000
        for index in 0..<4000 { pcm.floatChannelData![0][index] = 0.05 }
        var ticket: UUID?
        var saved: URL?
        pump.onRecording = { url in
            // Like the app callback, reserve before dispatching to the main actor.
            ticket = state.begin(.recording); saved = url
        }
        pump.onFailure = { state.fail($0) }
        pump.update(CaptureFlags(recording: true))
        pump.submit(pcm, hostTime: 0)
        pump.softPause()
        await pump.drain()
        XCTAssertNil(saved)
        pump.sealRecording()
        await pump.drain()
        let directory = try XCTUnwrap(saved)
        XCTAssertEqual(try AVAudioFile(forReading: directory.appendingPathComponent("chunk-00000.caf")).length, 4000)
        XCTAssertFalse(state.snapshot.canExit, "Disk barrier must not race database registration")
        XCTAssertEqual(state.snapshot.recordings, 1)
        let captions = state.begin(.captions)
        state.finish(try XCTUnwrap(ticket))
        XCTAssertFalse(state.snapshot.canExit, "Subtitles must finish independently")
        state.finish(captions)
        XCTAssertTrue(state.snapshot.canExit)
        // Repeating the quit path must not create another logical segment.
        pump.sealRecording(); await pump.drain()
        XCTAssertTrue(state.snapshot.canExit)
    }

    func testFailedRecordingPreventsAutomaticExitEvenWhenOtherWorkFinishes() {
        let state = ExitReadiness()
        let first = state.begin(.recording), second = state.begin(.recording)
        let captions = state.begin(.captions)
        state.finish(second, failure: "registration failed")
        state.finish(captions)
        XCTAssertFalse(state.snapshot.settled)
        state.finish(first)
        XCTAssertTrue(state.snapshot.settled)
        XCTAssertFalse(state.snapshot.canExit)
        state.finish(second) // A duplicate completion must not clear a failure.
        XCTAssertEqual(state.snapshot.failures, ["registration failed"])
    }

    func testDiskFailureIsVisibleBeforeAsyncUIUpdate() async throws {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try Data([1]).write(to: file)
        defer { try? FileManager.default.removeItem(at: file) }
        let state = ExitReadiness()
        let pump = CapturePump(audioRoot: file, referenceTime: 0)
        pump.onFailure = { state.fail($0) }
        pump.update(CaptureFlags(recording: true))
        pump.sealRecording(); await pump.drain()
        XCTAssertTrue(state.snapshot.settled)
        XCTAssertFalse(state.snapshot.canExit)
        XCTAssertEqual(state.snapshot.failures.count, 1)
    }
}
