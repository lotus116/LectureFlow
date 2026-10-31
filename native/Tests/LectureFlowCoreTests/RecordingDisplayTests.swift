import XCTest
@testable import LectureFlowCore

final class RecordingDisplayTests: XCTestCase {
    func testGeneratedRecordingUsesLocalTimeIncludingFractionalISOStamp() {
        for stamp in ["2026-10-31T06:06:28Z", "2026-10-31T06:06:28.123Z"] {
            let display = RecordingDisplay(originalName: "录制 · " + stamp, position: 2)
            XCTAssertEqual(display.title, "录音 03")
            let subtitle = display.subtitle(duration: "30 秒", locale: Locale(identifier: "zh_CN"),
                                            timeZone: TimeZone(secondsFromGMT: 8 * 3600)!)
            XCTAssertTrue(subtitle.contains("14:06"), subtitle)
            XCTAssertTrue(subtitle.hasSuffix(" · 30 秒"))
        }
    }

    func testImportedNamesMalformedStampsAndRecoveryWarningsAreNotRenamed() {
        for name in ["Lecture 01.wav", "录制 · broken", "恢复录音（部分音频待检查） · 2026-10-31T06:06:28Z"] {
            let display = RecordingDisplay(originalName: name, position: 0)
            XCTAssertEqual(display.title, name)
            XCTAssertNil(display.recordedAt)
            XCTAssertEqual(display.subtitle(duration: "7 分钟"), "7 分钟")
        }
    }
}
