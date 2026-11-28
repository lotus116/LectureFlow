import AppKit
import SwiftUI

/// Owns the sidebar so the toolbar toggle stays fixed (system split view adds its own).
struct LibrarySplitLayout<Rail: View, Sidebar: View, Detail: View>: View {
    @ThemePalette private var palette
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @AppStorage("librarySidebarWidth") private var preferredSidebarWidth = 0.0
    @Binding var sidebarVisible: Bool
    let rail: Rail
    let sidebar: Sidebar
    let detail: Detail
    init(sidebarVisible: Binding<Bool>, @ViewBuilder rail: () -> Rail, @ViewBuilder sidebar: () -> Sidebar,
         @ViewBuilder detail: () -> Detail) {
        _sidebarVisible = sidebarVisible
        self.rail = rail()
        self.sidebar = sidebar()
        self.detail = detail()
    }
    var body: some View {
        GeometryReader { geo in
            let compact = geo.size.width < 820
            // Zero preserves the pre-existing adaptive default until explicitly resized.
            let limit = max(180.0, min(360.0, geo.size.width - 45 - 6 - 480))
            let sidebarWidth = min(limit, max(180, preferredSidebarWidth > 0 ? preferredSidebarWidth : (compact ? 200 : 220)))
            HStack(spacing: 0) {
                rail.frame(width: 44).background(palette.sidebar)
                Rectangle().fill(palette.hairline).frame(width: 1)
                if sidebarVisible {
                    sidebar
                        .frame(width: sidebarWidth)
                        .background(palette.sidebar)
                    LibrarySidebarDivider(width: sidebarWidth, maximum: limit,
                                          color: NSColor(palette.hairline),
                                          update: { preferredSidebarWidth = $0 })
                        .frame(width: 6)
                        .background(palette.sidebar)
                }
                detail
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .background(palette.canvas)
                    .environment(\.lfCompactChrome, compact)
            }
            .background(palette.canvas)
        }
        .animation(reduceMotion ? nil : LFChrome.motion, value: sidebarVisible)
    }
}

private struct LFCompactChromeKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    var lfCompactChrome: Bool {
        get { self[LFCompactChromeKey.self] }
        set { self[LFCompactChromeKey.self] = newValue }
    }
}

/// Native event handling keeps dragging responsive without animating the reading area.
/// Arrow keys and accessibility increment/decrement provide a non-drag alternative.
private struct LibrarySidebarDivider: NSViewRepresentable {
    let width: Double
    let maximum: Double
    let color: NSColor
    let update: (Double) -> Void

    func makeNSView(context: Context) -> SidebarDividerView { SidebarDividerView() }
    func updateNSView(_ view: SidebarDividerView, context: Context) {
        view.currentWidth = width
        view.maximum = maximum
        view.lineColor = color
        view.updateWidth = update
        view.needsDisplay = true
    }
}

private final class SidebarDividerView: NSView {
    var currentWidth = 220.0
    var maximum = 360.0
    var lineColor = NSColor.separatorColor
    var updateWidth: (Double) -> Void = { _ in }
    private var dragOrigin: (x: CGFloat, width: Double)?
    override var acceptsFirstResponder: Bool { true }
    override var isFlipped: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override init(frame: NSRect) {
        super.init(frame: frame)
        toolTip = "拖动调整课程栏宽度；聚焦后用左右方向键调整"
        setAccessibilityElement(true)
        setAccessibilityRole(.splitter)
        setAccessibilityLabel("课程栏宽度")
        setAccessibilityIdentifier("library-sidebar-divider")
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .resizeLeftRight) }
    override func draw(_ dirtyRect: NSRect) {
        let focused = window?.firstResponder === self
        (focused ? NSColor.keyboardFocusIndicatorColor : lineColor).setFill()
        NSRect(x: bounds.midX - (focused ? 1 : 0.5), y: 0,
               width: focused ? 2 : 1, height: bounds.height).fill()
    }
    override func becomeFirstResponder() -> Bool { needsDisplay = true; return true }
    override func resignFirstResponder() -> Bool { needsDisplay = true; return true }
    override func mouseDown(with event: NSEvent) {
        window?.makeFirstResponder(self)
        dragOrigin = (event.locationInWindow.x, currentWidth)
    }
    override func mouseDragged(with event: NSEvent) {
        guard let origin = dragOrigin else { return }
        resize(origin.width + event.locationInWindow.x - origin.x)
    }
    override func mouseUp(with event: NSEvent) { dragOrigin = nil }
    override func keyDown(with event: NSEvent) {
        switch event.keyCode {
        case 123: resize(currentWidth - 10)
        case 124: resize(currentWidth + 10)
        default: super.keyDown(with: event)
        }
    }
    private func resize(_ requested: Double) {
        currentWidth = min(maximum, max(180, requested))
        updateWidth(currentWidth)
        needsDisplay = true
        NSAccessibility.post(element: self, notification: .valueChanged)
    }
    override func accessibilityValue() -> Any? { NSNumber(value: currentWidth) }
    override func accessibilityMinValue() -> Any? { NSNumber(value: 180) }
    override func accessibilityMaxValue() -> Any? { NSNumber(value: maximum) }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(setAccessibilityValue(_:)) { return true }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func setAccessibilityValue(_ value: Any?) {
        if let number = value as? NSNumber { resize(number.doubleValue) }
    }
    override func accessibilityPerformIncrement() -> Bool { resize(currentWidth + 10); return true }
    override func accessibilityPerformDecrement() -> Bool { resize(currentWidth - 10); return true }
}
