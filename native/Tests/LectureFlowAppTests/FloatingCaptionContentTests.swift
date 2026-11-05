import AppKit
import LectureFlowCore
import SwiftUI
import XCTest
@testable import LectureFlow

@MainActor private final class CaptionFixture: ObservableObject {
    @Published var captions: [LiveCaption] = []
}

private struct CaptionFixtureView: View {
    @ObservedObject var model: CaptionFixture
    var body: some View {
        ScrollViewReader { _ in
            FloatingCaptionContent(captions: model.captions, waiting: true, fontSize: 22, spacing: 6) {
                Text($0.chinese).font(.system(size: 22)).foregroundStyle(Color.green)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.black)
    }
}

final class FloatingCaptionContentTests: XCTestCase {
    @MainActor private func pixels(_ host: NSView) throws -> (waiting: Int, captions: Int) {
        host.layoutSubtreeIfNeeded()
        let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        var waiting = 0, captions = 0
        for y in stride(from: 0, to: bitmap.pixelsHigh, by: 2) {
            for x in stride(from: 0, to: bitmap.pixelsWide, by: 2) {
                guard let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) else { continue }
                let r = color.redComponent, g = color.greenComponent, b = color.blueComponent
                if min(r, g, b) > 0.35 && max(r, g, b) - min(r, g, b) < 0.08 { waiting += 1 }
                if g > 0.3 && g > r * 1.4 && g > b * 1.4 { captions += 1 }
            }
        }
        return (waiting, captions)
    }

    @MainActor func testEmptyToCaptionsAndBackNeverKeepsWaitingAsFooter() async throws {
        _ = NSApplication.shared
        let model = CaptionFixture()
        let host = NSHostingView(rootView: CaptionFixtureView(model: model))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 260),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        window.orderFront(nil)
        defer { window.close() }
        func caption(_ id: String) -> LiveCaption {
            LiveCaption(id: id, start: 0, end: 3, english: "Synthetic sentence.",
                        chinese: "合成字幕 \(id)", status: "stable", revision: 1, sessionID: "fixture")
        }
        // Inspect the rendered view: white empty-state text must disappear when
        // green fixture captions arrive. No accessibility permission is required.
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertGreaterThan(try pixels(host).waiting, 0)
        model.captions = [caption("first"), caption("second")]
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertGreaterThan(try pixels(host).captions, 0)
        XCTAssertEqual(try pixels(host).waiting, 0)
        model.captions.append(caption("third")) // Same revision, new identity.
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertGreaterThan(try pixels(host).captions, 0)
        XCTAssertEqual(try pixels(host).waiting, 0)
        model.captions = []
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertGreaterThan(try pixels(host).waiting, 0)
        XCTAssertEqual(try pixels(host).captions, 0)
        model.captions = [caption("new-session")]
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertGreaterThan(try pixels(host).captions, 0)
        XCTAssertEqual(try pixels(host).waiting, 0)
    }
}
