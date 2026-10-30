import AVFoundation
import Foundation

/// Tap copies are bounded; disk writing never waits on the inference pipe.
public final class CapturePump {
    private let queue = DispatchQueue(label: "LectureFlow.capture", qos: .userInitiated)
    private let lock = NSLock()
    private var pending = 0
    private var failed = false
    private let capacity = 32
    private var flags = CaptureFlags.paused
    private var writer: RecordingWriter?
    private var converter: AVAudioConverter?
    private let format = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!
    private let audioRoot: URL
    private let referenceTime: Double
    private let captureID: String
    private var lastEnd = 0.0
    public var onPCM: ((Data, Double, CaptureFlags) -> Void)?
    public var onMode: ((CaptureFlags) -> Void)?
    public var onRecording: ((URL) -> Void)?
    public var onDiscardedRecording: ((URL) -> Void)?
    public var onFailure: ((String) -> Void)?

    public init(audioRoot: URL, referenceTime: Double, captureID: String = UUID().uuidString) {
        self.audioRoot = audioRoot; self.referenceTime = referenceTime; self.captureID = captureID
    }

    private func fail(_ error: Error) {
        lock.lock(); let notify = !failed; failed = true; lock.unlock()
        if notify { onFailure?("音频保存遇到问题，已停止采集：" + error.localizedDescription) }
    }

    public func update(_ value: CaptureFlags) {
        queue.async {
            do {
                if self.flags.needsMicrophone && !value.needsMicrophone {
                    try self.flushConverter()
                }
                if !value.recording, let writer = self.writer {
                    if let path = try writer.finish() { self.onRecording?(path) }
                    self.writer = nil
                }
                if value.recording && self.writer == nil {
                    self.writer = try RecordingWriter(audioRoot: self.audioRoot, captureID: self.captureID)
                }
                self.flags = value
                self.onMode?(value)
            } catch { self.fail(error) }
        }
    }

    public func submit(_ buffer: AVAudioPCMBuffer, hostTime: Double) {
        guard buffer.frameLength > 0 else { return }
        lock.lock()
        if failed { lock.unlock(); return }
        if pending >= capacity {
            lock.unlock(); fail(CocoaError(.fileWriteOutOfSpace)); return
        }
        pending += 1; lock.unlock()
        guard let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else {
            lock.lock(); pending -= 1; lock.unlock()
            fail(CocoaError(.fileWriteOutOfSpace)); return
        }
        copy.frameLength = buffer.frameLength
        let src = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: buffer.audioBufferList))
        let dst = UnsafeMutableAudioBufferListPointer(copy.mutableAudioBufferList)
        for index in 0..<src.count {
            memcpy(dst[index].mData!, src[index].mData!, Int(src[index].mDataByteSize))
        }
        queue.async {
            defer { self.lock.lock(); self.pending -= 1; self.lock.unlock() }
            do { try self.consume(copy, hostTime: hostTime) }
            catch { self.fail(error) }
        }
    }

    private func consume(_ input: AVAudioPCMBuffer, hostTime: Double) throws {
        guard flags.needsMicrophone else { return }
        let output: AVAudioPCMBuffer
        if input.format == format { output = input }
        else {
            if converter?.inputFormat != input.format { converter = AVAudioConverter(from: input.format, to: format) }
            guard let converter else { throw CocoaError(.fileReadCorruptFile) }
            let count = AVAudioFrameCount(ceil(Double(input.frameLength) * 16000 / input.format.sampleRate)) + 64
            guard let converted = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: count) else {
                throw CocoaError(.fileWriteOutOfSpace)
            }
            var supplied = false
            var conversionError: NSError?
            converter.convert(to: converted, error: &conversionError) { _, status in
                if supplied { status.pointee = .noDataNow; return nil }
                supplied = true; status.pointee = .haveData; return input
            }
            if let conversionError { throw conversionError }
            output = converted
        }
        guard output.frameLength > 0 else { return }
        try writeNormalized(output, start: max(0, hostTime - referenceTime))
    }

    private func writeNormalized(_ output: AVAudioPCMBuffer, start: Double) throws {
        if flags.recording { try writer?.append(output, start: start) }
        if flags.needsASR {
            let bytes = Data(bytes: output.floatChannelData![0], count: Int(output.frameLength) * 4)
            onPCM?(bytes, start, flags)
        }
        lastEnd = start + Double(output.frameLength) / 16000
    }

    private func flushConverter() throws {
        guard let converter else { return }
        defer { self.converter = nil }
        for _ in 0..<8 {
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1024)!
            var error: NSError?
            let status = converter.convert(to: buffer, error: &error) { _, state in
                state.pointee = .endOfStream; return nil
            }
            if let error { throw error }
            if buffer.frameLength > 0 { try writeNormalized(buffer, start: lastEnd) }
            if status == .endOfStream || buffer.frameLength == 0 { return }
        }
    }

    public func finish() {
        update(.paused)
        queue.sync {} // bounded pending input; this never waits for a model.
    }

    /// Cancellation seals the media first; the library then moves it to recoverable trash.
    public func cancelRecording() {
        queue.async {
            do {
                if !self.flags.needsASR { try self.flushConverter() }
                if let writer = self.writer, let path = try writer.finish() {
                    self.onDiscardedRecording?(path)
                }
                self.writer = nil
                self.flags.recording = false
                self.onMode?(self.flags)
            } catch { self.fail(error) }
        }
    }
}
