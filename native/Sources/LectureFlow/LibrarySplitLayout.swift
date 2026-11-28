import SwiftUI

/// Owns the sidebar so the toolbar toggle stays fixed (system split view adds its own).
struct LibrarySplitLayout<Rail: View, Sidebar: View, Detail: View>: View {
    @ThemePalette private var palette
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
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
            let sidebarWidth = compact ? 200.0 : 220.0
            HStack(spacing: 0) {
                rail.frame(width: 44).background(palette.sidebar)
                Rectangle().fill(palette.hairline).frame(width: 1)
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
