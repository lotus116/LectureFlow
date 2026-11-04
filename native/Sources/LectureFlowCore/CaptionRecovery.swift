import Foundation

/// Text is deliberately absent: silence and repeated words are valid classroom input.
public struct CaptionProgress: Sendable {
    public private(set) var inputEnd = 0.0
    public private(set) var processedEnd = 0.0
    public private(set) var lastProgress: Double
    public private(set) var ready = false
    private var longestInference = 0.0
    public init(now: Double) { lastProgress = now }
    public mutating func input(end: Double) {
        if end.isFinite { inputEnd = max(inputEnd, end) }
    }
    public mutating func prepared(now: Double) { ready = true; lastProgress = now }
    public mutating func processed(end: Double, duration: Double, now: Double) {
        guard end.isFinite, duration.isFinite, duration >= 0, end >= processedEnd else { return }
        processedEnd = end; lastProgress = now
        longestInference = max(longestInference, duration)
    }
    public var timeout: Double { min(600, max(180, longestInference * 8)) }
    public func stalled(now: Double) -> Bool {
        if !ready { return now - lastProgress > 300 }
        return inputEnd > processedEnd + 0.1 && now - lastProgress > timeout
    }
}

public struct CaptionRecoveryBudget: Sendable {
    public private(set) var attempts = 0
    public init() {}
    public mutating func nextDelay() -> Double? {
        guard attempts < 3 else { return nil }
        attempts += 1
        return pow(2, Double(attempts))
    }
}

public enum CaptionEventPolicy {
    public static func accepts(token: UUID, activeToken: UUID?, eventSession: String?,
                               currentSession: String?, drainingSession: String?,
                               captureStarted: Bool) -> Bool {
        guard let eventSession else { return false }
        if token == activeToken { return eventSession == currentSession }
        guard activeToken == nil, drainingSession == eventSession else { return false }
        return !captureStarted || currentSession == eventSession
    }
}
