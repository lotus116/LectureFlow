import AppKit
import SwiftUI

/// Disables the system (usually blue) table selection fill so SwiftUI can draw LFChrome accents.
struct SidebarSelectionStyle: NSViewRepresentable {
    func makeNSView(context: Context) -> Marker { Marker() }
    func updateNSView(_ view: Marker, context: Context) { view.apply() }

    final class Marker: NSView {
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            apply()
        }

        override func viewDidMoveToSuperview() {
            super.viewDidMoveToSuperview()
            apply()
        }

        func apply() {
            DispatchQueue.main.async { [weak self] in
                guard let table = self?.enclosingTableView() else { return }
                if table.selectionHighlightStyle != .none {
                    table.selectionHighlightStyle = .none
                }
            }
        }

        private func enclosingTableView() -> NSTableView? {
            var current: NSView? = self
            while let view = current {
                if let table = view as? NSTableView { return table }
                current = view.superview
            }
            return nil
        }
    }
}
