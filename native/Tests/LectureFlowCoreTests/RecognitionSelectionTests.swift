import XCTest
@testable import LectureFlowCore

final class RecognitionSelectionTests: XCTestCase {
    func testChoiceDuringStartupIsNotOverwrittenByOldReadyMessage() {
        var choice = RecognitionSelection()
        choice.request("small"); choice.request("large")
        choice.acknowledge("small")
        XCTAssertEqual(choice.requested, "large")
        XCTAssertEqual(choice.pending, "large")
        choice.acknowledge("large")
        XCTAssertNil(choice.pending)
    }
    func testChangingBackCancelsPendingChoiceButLateAcknowledgementStaysVisible() {
        var choice = RecognitionSelection()
        choice.request("small"); choice.acknowledge("small")
        choice.request("large"); choice.request("small")
        XCTAssertNil(choice.pending)
        choice.acknowledge("large")
        XCTAssertEqual(choice.pending, "small")
        choice.acknowledge("small")
        XCTAssertNil(choice.pending)
    }
    func testEndClearsChoiceForNextCapture() {
        var choice = RecognitionSelection()
        choice.request("large"); choice.reset()
        XCTAssertNil(choice.requested); XCTAssertNil(choice.active)
    }
}

final class RecognitionPreferenceTests: XCTestCase {
    func testMigrationAndExplicitStandardChoiceSurviveReopening() {
        let suite = "RecognitionPreferenceTests." + UUID().uuidString
        let first = UserDefaults(suiteName: suite)!
        defer { first.removePersistentDomain(forName: suite) }
        XCTAssertEqual(RecognitionPreference.load(from: first), "asr-large")
        RecognitionPreference.save("asr-small", to: first)
        let reopened = UserDefaults(suiteName: suite)!
        XCTAssertEqual(RecognitionPreference.load(from: reopened), "asr-small")
        RecognitionPreference.save("asr-large", to: reopened)
        XCTAssertEqual(RecognitionPreference.load(from: first), "asr-large")
        RecognitionPreference.save("missing-model", to: reopened)
        XCTAssertEqual(RecognitionPreference.load(from: first), "asr-large")
    }
}
