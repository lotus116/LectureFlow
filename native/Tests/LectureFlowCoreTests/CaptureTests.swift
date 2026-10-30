import AVFoundation
import Foundation
import XCTest
@testable import LectureFlowCore

final class CaptureTests: XCTestCase {
    func testAllEightIndependentModes() {
        for bits in 0..<8 {
            let flags = CaptureFlags(recording: bits & 1 != 0, english: bits & 2 != 0,
                                     translate: bits & 4 != 0)
            XCTAssertEqual(flags.needsMicrophone, bits != 0)
            XCTAssertEqual(flags.needsASR, bits & 6 != 0)
            XCTAssertEqual(flags.recording, bits & 1 != 0)
        }
    }
    private func pcm(frames: AVAudioFrameCount) -> AVAudioPCMBuffer {
        let format = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!
        let value = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        value.frameLength = frames
        for index in 0..<Int(frames) { value.floatChannelData![0][index] = 0.05 }
        return value
    }
    func testChunkBoundaryKeepsEveryFrameInOneLogicalRecording() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let writer = try RecordingWriter(audioRoot: root, chunkFrames: 1600, captureID: "capture-anchor")
        try writer.append(pcm(frames: 4000), start: 12.5)
        let directory = try XCTUnwrap(writer.finish())
        let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "caf" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
        XCTAssertEqual(files.count, 3)
        let lengths = try files.map { try AVAudioFile(forReading: $0).length }
        XCTAssertEqual(lengths, [1600, 1600, 800])
        let descriptor = try JSONSerialization.jsonObject(with: Data(contentsOf: directory.appendingPathComponent("capture.json"))) as! [String: Any]
        XCTAssertEqual(descriptor["sealed"] as? Bool, true)
        XCTAssertEqual(descriptor["frames"] as? Int, 4000)
        XCTAssertEqual(descriptor["capture_id"] as? String, "capture-anchor")
        XCTAssertEqual(descriptor["start_offset_s"] as? Double, 12.5)
    }
    func testTranslationWithoutRecordingProducesNoAudioFiles() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let pump = CapturePump(audioRoot: root, referenceTime: 10)
        var received = 0
        pump.onPCM = { data, start, flags in
            received += data.count
            XCTAssertEqual(start, 0)
            XCTAssertTrue(flags.translate)
            XCTAssertFalse(flags.recording)
        }
        pump.update(CaptureFlags(translate: true))
        pump.submit(pcm(frames: 4000), hostTime: 10)
        pump.finish()
        XCTAssertEqual(received, 16000)
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))
    }
    func testSoftPauseKeepsOneSegmentUntilSealThenAppendCreatesAnother() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let pump = CapturePump(audioRoot: root, referenceTime: 0)
        var sealed: [URL] = []
        pump.onRecording = { sealed.append($0) }
        pump.onPCM = { _, _, _ in XCTFail("Recording alone must not run inference") }
        pump.update(CaptureFlags(recording: true))
        pump.submit(pcm(frames: 4000), hostTime: 0)
        pump.softPause()
        pump.barrier()
        XCTAssertEqual(sealed.count, 0)
        pump.update(CaptureFlags(recording: true))
        pump.submit(pcm(frames: 4000), hostTime: 0.5)
        pump.sealRecording()
        pump.barrier()
        XCTAssertEqual(sealed.count, 1)
        pump.update(CaptureFlags(recording: true))
        pump.submit(pcm(frames: 4000), hostTime: 1)
        pump.sealRecording()
        pump.barrier()
        XCTAssertEqual(sealed.count, 2)
        XCTAssertNotEqual(sealed[0], sealed[1])
    }

    func testResamplerFlushesTailBeforeSealingRecording() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let pump = CapturePump(audioRoot: root, referenceTime: 0)
        let format = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 48000)!
        buffer.frameLength = 48000
        for index in 0..<48000 { buffer.floatChannelData![0][index] = 0.05 }
        var directory: URL?
        pump.onRecording = { directory = $0 }
        pump.onFailure = { XCTFail($0) }
        pump.update(CaptureFlags(recording: true))
        pump.submit(buffer, hostTime: 0)
        pump.sealRecording()
        pump.barrier()
        let url = try XCTUnwrap(directory).appendingPathComponent("chunk-00000.caf")
        let audio = try AVAudioFile(forReading: url)
        XCTAssertEqual(audio.length, 16000)
    }

    func testEveryModeRoutesOnlyToRequestedSinks() throws {
        for bits in 0..<8 {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: root) }
            let flags = CaptureFlags(recording: bits & 1 != 0, english: bits & 2 != 0,
                                     translate: bits & 4 != 0)
            let pump = CapturePump(audioRoot: root, referenceTime: 0)
            var inferredBytes = 0
            var recordings = 0
            pump.onPCM = { data, _, _ in inferredBytes += data.count }
            pump.onRecording = { _ in recordings += 1 }
            pump.onFailure = { XCTFail($0) }
            pump.update(flags)
            pump.submit(pcm(frames: 4000), hostTime: 0)
            if flags.recording {
                pump.sealRecording()
            } else {
                pump.finish()
            }
            pump.barrier()
            XCTAssertEqual(inferredBytes > 0, flags.needsASR)
            XCTAssertEqual(recordings, flags.recording ? 1 : 0)
        }
    }

    func testBrokenInferencePipeThrowsInsteadOfTerminatingRecordingProcess() throws {
        let pipe = Pipe()
        try protectInferencePipe(pipe.fileHandleForWriting)
        try pipe.fileHandleForReading.close()
        XCTAssertThrowsError(try pipe.fileHandleForWriting.write(contentsOf: Data([1, 2, 3])))
        try pipe.fileHandleForWriting.close()
    }

    func testCancelSealsOnlyCurrentRecordingAndKeepsCaptionAudioRunning() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let pump = CapturePump(audioRoot: root, referenceTime: 0)
        var discarded: [URL] = []
        var bytes = 0
        pump.onRecording = { _ in XCTFail("Cancelled segment must use the discard route") }
        pump.onDiscardedRecording = { discarded.append($0) }
        pump.onPCM = { data, _, _ in bytes += data.count }
        pump.onFailure = { XCTFail($0) }
        pump.update(.all)
        pump.submit(pcm(frames: 4000), hostTime: 0)
        pump.cancelRecording()
        pump.submit(pcm(frames: 4000), hostTime: 0.25)
        pump.finish()
        XCTAssertEqual(discarded.count, 1)
        XCTAssertEqual(bytes, 32000)
        let audio = try AVAudioFile(forReading: discarded[0].appendingPathComponent("chunk-00000.caf"))
        XCTAssertEqual(audio.length, 4000)
    }
}
