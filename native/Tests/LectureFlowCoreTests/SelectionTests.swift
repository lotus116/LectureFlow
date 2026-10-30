import XCTest
@testable import LectureFlowCore

final class SelectionTests: XCTestCase {
    func testOldTranscriptCannotBeAppliedToSameNamedNewClass() {
        var fence = ResultFence()
        let old = fence.ticket(owner: "deleted-course-lecture", version: "old-transcript")
        fence.invalidate()
        XCTAssertFalse(fence.accepts(old, owner: "same-name-new-lecture", version: nil))
        XCTAssertFalse(fence.accepts(old, owner: "deleted-course-lecture", version: "old-transcript"))
        let current = fence.ticket(owner: "same-name-new-lecture", version: "new-transcript")
        XCTAssertTrue(fence.accepts(current, owner: "same-name-new-lecture", version: "new-transcript"))
    }
    func testSwitchingVersionsRejectsLateResponse() {
        var fence = ResultFence()
        let old = fence.ticket(owner: "lecture", version: "v1")
        fence.invalidate()
        XCTAssertFalse(fence.accepts(old, owner: "lecture", version: "v2"))
    }
}
