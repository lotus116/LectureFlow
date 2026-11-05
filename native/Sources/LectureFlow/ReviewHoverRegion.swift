import AppKit
import SwiftUI

/// Floating captions deliberately do not activate the app; reveal controls even
/// while another app has focus, without intercepting text selection or clicks.
struct ReviewHoverRegion: NSViewRepresentable {
    let changed: (Bool) -> Void
    func makeNSView(context: Context) -> HoverView { HoverView(changed: changed) }
    func updateNSView(_ view: HoverView, context: Context) { view.changed = changed }

    final class HoverView: NSView {
        var changed: (Bool) -> Void
        private var area: NSTrackingArea?
        init(changed: @escaping (Bool) -> Void) {
            self.changed = changed
            super.init(frame: .zero)
        }
        required init?(coder: NSCoder) { nil }
        override func hitTest(_ point: NSPoint) -> NSView? { nil }
        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            if let area { removeTrackingArea(area) }
            let next = NSTrackingArea(rect: .zero,
                options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                owner: self, userInfo: nil)
            addTrackingArea(next); area = next
        }
        override func mouseEntered(with event: NSEvent) { changed(true) }
        override func mouseExited(with event: NSEvent) { changed(false) }
    }
}
