import AVFoundation
import Darwin
import Foundation

/// Used only on the capture queue. Each CAF is independently recoverable.
public final class RecordingWriter {
    public let directory: URL
    private let id: String
    private let created: String
    private let captureID: String
    private var startOffset = 0.0
    private let chunkFrames: AVAudioFrameCount
    private var file: AVAudioFile?
    private var fileFrames: AVAudioFrameCount = 0
    private var chunkIndex = 0
    private var totalFrames: Int64 = 0
    private var lockFD: Int32 = -1
    private var sealed = false

    public init(audioRoot: URL, chunkFrames: AVAudioFrameCount = 4_800_000,
                captureID: String = UUID().uuidString) throws {
        guard chunkFrames > 0 else { throw CocoaError(.fileWriteInvalidFileName) }
        self.chunkFrames = chunkFrames
        self.captureID = captureID
        id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        created = ISO8601DateFormatter().string(from: Date())
        directory = audioRoot.appendingPathComponent("live-" + id)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        lockFD = open(directory.appendingPathComponent(".recording.lock").path,
                      O_CREAT | O_RDWR | O_CLOEXEC, S_IRUSR | S_IWUSR)
        guard lockFD >= 0, flock(lockFD, LOCK_EX | LOCK_NB) == 0 else {
            if lockFD >= 0 { close(lockFD) }; lockFD = -1
            throw CocoaError(.fileWriteNoPermission)
        }
        do { try checkpoint() }
        catch { close(lockFD); lockFD = -1; throw error }
    }

    deinit { if lockFD >= 0 { close(lockFD) } }

    private func checkpoint() throws {
        let object: [String: Any] = ["id": id, "created_at": created, "sealed": sealed,
                                      "frames": totalFrames, "sample_rate": 16000,
                                      "capture_id": captureID, "start_offset_s": startOffset]
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        try data.write(to: directory.appendingPathComponent("capture.json"), options: .atomic)
    }

    public func append(_ buffer: AVAudioPCMBuffer, start: Double = 0) throws {
        guard !sealed, buffer.format.sampleRate == 16000,
              buffer.format.channelCount == 1, let input = buffer.floatChannelData else {
            throw CocoaError(.fileWriteInapplicableStringEncoding)
        }
        if totalFrames == 0 { startOffset = start }
        var offset: AVAudioFrameCount = 0
        while offset < buffer.frameLength {
            if file == nil {
                let settings: [String: Any] = [
                    AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000,
                    AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16,
                    AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false
                ]
                let path = directory.appendingPathComponent(String(format: "chunk-%05d.caf", chunkIndex))
                file = try AVAudioFile(forWriting: path, settings: settings,
                                       commonFormat: .pcmFormatFloat32, interleaved: false)
                chunkIndex += 1; fileFrames = 0
            }
            let count = min(buffer.frameLength - offset, chunkFrames - fileFrames)
            guard let part = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: count) else {
                throw CocoaError(.fileWriteOutOfSpace)
            }
            part.frameLength = count
            memcpy(part.floatChannelData![0], input[0].advanced(by: Int(offset)), Int(count) * 4)
            try file!.write(from: part)
            offset += count; fileFrames += count; totalFrames += Int64(count)
            if fileFrames == chunkFrames {
                let url = file!.url
                file = nil
                try synchronize(url)
                try checkpoint()
            }
        }
    }

    public func finish() throws -> URL? {
        if sealed { return totalFrames > 0 ? directory : nil }
        let url = file?.url
        file = nil
        if let url { try synchronize(url) }
        sealed = true
        do { try checkpoint() }
        catch { sealed = false; throw error }
        if lockFD >= 0 { close(lockFD); lockFD = -1 }
        return totalFrames > 0 ? directory : nil
    }

    private func synchronize(_ url: URL) throws {
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }
        try handle.synchronize()
    }
}
