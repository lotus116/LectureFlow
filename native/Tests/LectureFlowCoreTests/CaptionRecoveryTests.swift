import XCTest
@testable import LectureFlowCore

final class CaptionRecoveryTests: XCTestCase {
    func testSilenceAndSlowInferenceDoNotTriggerRecovery() {
        var health = CaptionProgress(now: 0)
        health.prepared(now: 1)
        for second in 1...1000 {
            health.input(end: Double(second))
            health.processed(end: Double(second), duration: 0.2, now: Double(second))
            XCTAssertFalse(health.stalled(now: Double(second)))
        }
        health.input(end: 1001)
        XCTAssertFalse(health.stalled(now: 1100))
        XCTAssertTrue(health.stalled(now: 1181))
    }
    func testAdaptiveDeadlineAndNoAudioIdle() {
        var health = CaptionProgress(now: 0)
        health.prepared(now: 0)
        health.processed(end: 1, duration: 30, now: 30)
        XCTAssertFalse(health.stalled(now: 10000))
        health.input(end: 2)
        XCTAssertFalse(health.stalled(now: 269))
        XCTAssertTrue(health.stalled(now: 271))
    }
    func testFailedProcessEventsCannotEnterReplacementOrNewSession() {
        let old = UUID(), replacement = UUID()
        XCTAssertFalse(CaptionEventPolicy.accepts(token: old, activeToken: replacement,
            eventSession: "same", currentSession: "same", drainingSession: nil, captureStarted: true))
        XCTAssertTrue(CaptionEventPolicy.accepts(token: old, activeToken: nil,
            eventSession: "same", currentSession: "same", drainingSession: "same", captureStarted: true))
        XCTAssertTrue(CaptionEventPolicy.accepts(token: old, activeToken: nil,
            eventSession: "same", currentSession: nil, drainingSession: "same", captureStarted: false))
        XCTAssertFalse(CaptionEventPolicy.accepts(token: old, activeToken: nil,
            eventSession: "same", currentSession: "new", drainingSession: "same", captureStarted: true))
        XCTAssertFalse(CaptionEventPolicy.accepts(token: old, activeToken: nil,
            eventSession: "same", currentSession: "same", drainingSession: nil, captureStarted: true))
    }

    func testRecoveryHasBackoffAndLifetimeLimit() {
        var budget = CaptionRecoveryBudget()
        XCTAssertEqual(budget.nextDelay(), 2)
        XCTAssertEqual(budget.nextDelay(), 4)
        XCTAssertEqual(budget.nextDelay(), 8)
        XCTAssertNil(budget.nextDelay())
        XCTAssertNil(budget.nextDelay())
    }
}
