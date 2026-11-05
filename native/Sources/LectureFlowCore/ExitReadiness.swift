import Foundation

/// Tickets are acquired on the producing queue, before dispatching async persistence.
/// A capture barrier alone cannot prove the database registration has completed.
public final class ExitReadiness: @unchecked Sendable {
    public enum Work: Sendable { case recording, captions }
    public struct Snapshot: Sendable {
        public let recordings: Int
        public let captions: Int
        public let failures: [String]
        public var settled: Bool { recordings == 0 && captions == 0 }
        public var canExit: Bool { settled && failures.isEmpty }
    }
    private let lock = NSLock()
    private var pending: [UUID: Work] = [:]
    private var failures: [String] = []
    public init() {}
    @discardableResult public func begin(_ work: Work) -> UUID {
        lock.lock(); defer { lock.unlock() }
        let id = UUID(); pending[id] = work; return id
    }
    public func finish(_ id: UUID, failure: String? = nil) {
        lock.lock(); defer { lock.unlock() }
        guard pending.removeValue(forKey: id) != nil else { return }
        if let failure, !failures.contains(failure) { failures.append(failure) }
    }
    public func fail(_ message: String) {
        lock.lock(); defer { lock.unlock() }
        if !failures.contains(message) { failures.append(message) }
    }
    public var snapshot: Snapshot {
        lock.lock(); defer { lock.unlock() }
        return Snapshot(recordings: pending.values.filter { $0 == .recording }.count,
                        captions: pending.values.filter { $0 == .captions }.count, failures: failures)
    }
}

/// A stopped message is insufficient until stdout is drained and the child exits.
public struct CaptionCompletion: Sendable {
    private var stopped = false
    private var failed = false
    public init() {}
    public mutating func observe(type: String, pendingTranslations: Int? = nil, pendingAccent: Int? = nil) {
        if type == "error" { failed = true }
        if type == "stopped" {
            stopped = true
            if let pendingTranslations, pendingTranslations != 0 { failed = true }
            if let pendingAccent, pendingAccent != 0 { failed = true }
        }
    }
    public func succeeded(exitStatus: Int32) -> Bool { stopped && !failed && exitStatus == 0 }
}
