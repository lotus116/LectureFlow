import Foundation

public enum CapturePreset: String, CaseIterable, Codable, Sendable {
    case classroom, recording, subtitles
    public var title: String {
        switch self {
        case .classroom: return "录音与双语字幕"
        case .recording: return "仅录音"
        case .subtitles: return "仅字幕，不保存录音"
        }
    }
    public var flags: CaptureFlags {
        switch self {
        case .classroom: return .all
        case .recording: return CaptureFlags(recording: true)
        case .subtitles: return CaptureFlags(english: true, translate: true)
        }
    }
}

/// UI lifecycle is independent of a writer, inference child, and saved lecture files.
/// Persist a checkpoint at boundaries; restoring never opens the microphone.
public struct ClassroomSession: Codable, Equatable, Sendable {
    public enum State: String, Codable, Sendable { case idle, preparing, running, paused, ended }
    public private(set) var state: State = .idle
    public private(set) var desired: CaptureFlags = .all
    private var accumulated: Double = 0
    private var runningSince: Double?
    public init() {}
    public var canConfigure: Bool { state != .running && state != .preparing }
    public var hasSession: Bool { state != .idle && state != .ended }
    public var modeTitle: String {
        CapturePreset.allCases.first { $0.flags == desired }?.title ?? "自定义记录方式"
    }
    public var primaryTitle: String {
        switch state {
        case .running, .preparing: return "暂停"
        case .paused: return "继续"
        case .idle, .ended: return desired.recording ? "开始录音" : "开启字幕"
        }
    }
    @discardableResult public mutating func configure(_ flags: CaptureFlags) -> Bool {
        guard canConfigure, flags.needsMicrophone else { return false }
        desired = flags; return true
    }
    @discardableResult public mutating func begin() -> Bool {
        guard canConfigure, desired.needsMicrophone else { return false }
        if state == .idle || state == .ended { accumulated = 0 }
        state = .preparing; runningSince = nil; return true
    }
    public mutating func started(at time: Double) {
        guard state == .preparing else { return }
        runningSince = time; state = .running
    }
    public func duration(at time: Double) -> Double {
        let delta = runningSince.map { max(0, time - $0) } ?? 0
        return max(0, min(31_536_000, accumulated + delta))
    }
    public mutating func pause(at time: Double) {
        guard state == .running || state == .preparing else { return }
        accumulated = duration(at: time); runningSince = nil; state = .paused
    }
    public mutating func end(at time: Double) {
        guard state != .idle else { return }
        accumulated = duration(at: time); runningSince = nil; state = .ended
    }
    public func checkpoint(at time: Double) -> ClassroomSession {
        var saved = self
        saved.accumulated = duration(at: time); saved.runningSince = nil
        if state == .running || state == .preparing { saved.state = .paused }
        return saved
    }
    public func restored() -> ClassroomSession {
        var safe = self
        safe.runningSince = nil
        if !safe.accumulated.isFinite { safe.accumulated = 0 }
        safe.accumulated = max(0, min(31_536_000, safe.accumulated))
        if state == .running || state == .preparing { safe.state = .paused }
        return safe
    }
}
