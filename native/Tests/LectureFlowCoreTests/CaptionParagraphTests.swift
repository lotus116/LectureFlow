import XCTest
@testable import LectureFlowCore

final class CaptionParagraphTests: XCTestCase {
    func testBoundedGroupingKeepsOriginalFragmentsAndGaps() {
        let fragments = (0..<8).map { index in
            LiveCaption(id: "\(index)", start: Double(index), end: Double(index + 1),
                        english: "not yet", chinese: "还没有", status: "stable", revision: 1,
                        sessionID: "session")
        }
        let paragraphs = LiveCaption.paragraphs(fragments)
        XCTAssertEqual(paragraphs.count, 3)
        XCTAssertEqual(paragraphs[0].start, 0)
        XCTAssertEqual(paragraphs[0].end, 3)
        XCTAssertEqual(paragraphs[0].english, "not yet not yet not yet")
        XCTAssertEqual(fragments.count, 8)
        let gap = LiveCaption(id: "gap", start: 10, end: 11, english: "correction",
                              chinese: "纠正", status: "stable", revision: 1, sessionID: "session")
        XCTAssertEqual(LiveCaption.paragraphs(fragments + [gap]).count, 4)
    }
    func testDifferentSessionsAndCompleteSentencesStaySeparate() {
        let first = LiveCaption(id: "a", start: 0, end: 1, english: "No.", chinese: "不。",
                                status: "stable", revision: 1, sessionID: "one")
        let second = LiveCaption(id: "b", start: 1, end: 2, english: "Correction", chinese: "纠正",
                                 status: "stable", revision: 1, sessionID: "two")
        XCTAssertEqual(LiveCaption.paragraphs([first, second]).count, 2)
    }
}
