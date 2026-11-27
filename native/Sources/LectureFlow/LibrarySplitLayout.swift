import SwiftUI

/// Owns the sidebar so the toolbar toggle stays fixed (system split view adds its own).
struct LibrarySplitLayout<Sidebar: View, Detail: View>: View {
    @ThemePalette private var palette
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Binding var sidebarVisible: Bool
    let sidebar: Sidebar
    let detail: Detail
    init(sidebarVisible: Binding<Bool>, @ViewBuilder sidebar: () -> Sidebar,
         @ViewBuilder detail: () -> Detail) {
        _sidebarVisible = sidebarVisible
        self.sidebar = sidebar()
        self.detail = detail()
    }
    var body: some View {
        GeometryReader { geo in
            let compact = geo.size.width < 820
            let sidebarWidth = compact ? LFChrome.sidebarCompact : LFChrome.sidebarIdeal
            HStack(spacing: 0) {
                if sidebarVisible {
                    sidebar
                        .frame(width: sidebarWidth)
                        .background(palette.sidebar)
                    Rectangle()
                        .fill(palette.hairline)
                        .frame(width: 1)
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
