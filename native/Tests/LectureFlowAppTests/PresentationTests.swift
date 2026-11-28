import AppKit
import SwiftUI
import XCTest
@testable import LectureFlow

final class PresentationTests: XCTestCase {
    func testDiffSemanticsAreAvailableWithoutColor() {
        XCTAssertEqual(LFReviewChangeKind("- observations").accessibleDescription("- observations"), "删除，observations")
        XCTAssertEqual(LFReviewChangeKind("+ measurements").accessibleDescription("+ measurements"), "新增，measurements")
        XCTAssertEqual(LFReviewChangeKind("uncertain").accessibleDescription("uncertain"), "uncertain")
    }

    func testSemanticInkContrastOnActualSurfaces() {
        func rgb(_ hex: UInt32) -> [Double] { [16, 8, 0].map { Double((hex >> $0) & 255) / 255 } }
        func lum(_ rgb: [Double]) -> Double {
            zip(rgb, [0.2126, 0.7152, 0.0722]).reduce(0) { sum, pair in
                sum + pair.1 * (pair.0 <= 0.04045 ? pair.0 / 12.92 : pow((pair.0 + 0.055) / 1.055, 2.4))
            }
        }
        for theme in LFTheme.allCases {
            for dark in [false, true] {
                let palette = LFPalette(theme: theme, dark: dark)
                for role in [LFPalette.Role.diffAdded, .diffRemoved, .warningInk, .dangerInk] {
                    let foreground = rgb(palette.hex(role))
                    for surface in [LFPalette.Role.canvas, .panel, .recorder] + (dark ? [.floating] : []) {
                        let base = rgb(palette.hex(surface))
                        // Difference-token fill is 6% of its own text ink.
                        let background = zip(base, foreground).map { $0.0 * 0.94 + $0.1 * 0.06 }
                        let a = lum(foreground), b = lum(background)
                        let ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
                        XCTAssertGreaterThanOrEqual(ratio, 4.5, "\(theme) \(dark) \(role) \(surface): \(ratio)")
                    }
                }
            }
        }
    }

    @MainActor func testLongStatusGrowsInsteadOfClippingAtOneLine() async {
        _ = NSApplication.shared
        let content = LFStatusBanner(message: "字幕处理中断，正在自动恢复；少量译文可能未完成，可用录音课后转写。A long recovery message must remain visible at a narrow window width.", tone: .attention) {
            Button("重试") {}
        }.frame(width: 320)
        let host = NSHostingView(rootView: content)
        host.layoutSubtreeIfNeeded()
        XCTAssertGreaterThan(host.fittingSize.height, 36)
        XCTAssertEqual(host.fittingSize.width, 320, accuracy: 1)
    }
}
