import XCTest
@testable import LectureFlowCore

final class SessionTests: XCTestCase {
    func testPauseResumeKeepsModeAndExcludesPausedTime() {
        var session = ClassroomSession()
        XCTAssertTrue(session.configure(CapturePreset.recording.flags))
        XCTAssertTrue(session.begin()); session.started(at: 10)
        XCTAssertFalse(session.begin())
        XCTAssertFalse(session.configure(.all))
        session.pause(at: 20)
        XCTAssertEqual(session.duration(at: 200), 10)
        XCTAssertEqual(session.desired, CapturePreset.recording.flags)
        XCTAssertTrue(session.begin()); session.started(at: 200)
        session.pause(at: 205)
        XCTAssertEqual(session.duration(at: 500), 15)
    }
    func testEndPreservesDurationAndNewStartResetsTimer() {
        var session = ClassroomSession()
        _ = session.begin(); session.started(at: 10); session.end(at: 30)
        XCTAssertEqual(session.state, .ended)
        XCTAssertEqual(session.duration(at: 100), 20)
        _ = session.begin(); session.started(at: 100)
        XCTAssertEqual(session.duration(at: 101), 1)
    }
    func testRestartRestoresPausedAndCannotStartByItself() throws {
        var session = ClassroomSession()
        _ = session.begin(); session.started(at: 5)
        let data = try JSONEncoder().encode(session.checkpoint(at: 25))
        let restored = try JSONDecoder().decode(ClassroomSession.self, from: data).restored()
        XCTAssertEqual(restored.state, .paused)
        XCTAssertEqual(restored.duration(at: 9999), 20)
    }
    func testPauseOrEndDuringPermissionRequestRejectsLateStart() {
        var session = ClassroomSession()
        _ = session.begin(); session.pause(at: 5); session.started(at: 10)
        XCTAssertEqual(session.state, .paused)
        _ = session.begin(); session.end(at: 11); session.started(at: 12)
        XCTAssertEqual(session.state, .ended)
    }
    func testPrivacyModesAndAllSwitchCombinationsSurviveResume() {
        for bits in 1..<8 {
            let flags = CaptureFlags(recording: bits & 1 != 0, english: bits & 2 != 0, translate: bits & 4 != 0)
            var session = ClassroomSession()
            XCTAssertTrue(session.configure(flags)); _ = session.begin()
            session.started(at: 0); session.pause(at: 1); _ = session.begin()
            XCTAssertEqual(session.desired, flags)
        }
        var idle = ClassroomSession()
        XCTAssertFalse(idle.configure(.paused))
    }
    func testMissingModelsNeverTurnRecordingOnOrEnableTranslationWithoutASR() {
        for bits in 1..<8 {
            let desired = CaptureFlags(recording: bits & 1 != 0, english: bits & 2 != 0, translate: bits & 4 != 0)
            for asr in [false, true] {
                for translation in [false, true] {
                    let effective = desired.available(asr: asr, translation: translation)
                    XCTAssertEqual(effective.recording, desired.recording)
                    XCTAssertEqual(effective.english, desired.english && asr)
                    XCTAssertEqual(effective.translate, desired.translate && asr && translation)
                }
            }
        }
    }
}
