import SwiftUI

struct MainWindowMarker: NSViewRepresentable {
    func makeNSView(context: Context) -> Marker { Marker() }
    func updateNSView(_ view: Marker, context: Context) {}
    final class Marker: NSView {
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            window?.identifier = NSUserInterfaceItemIdentifier("LectureFlowMain")
        }
    }
}
