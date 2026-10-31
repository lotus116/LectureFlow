import SwiftUI

/// Owns the sidebar so the toolbar toggle stays fixed (system split view adds its own).
struct LibrarySplitLayout<Sidebar: View, Detail: View>: View {
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
        HStack(spacing: 0) {
            if sidebarVisible {
                sidebar
                    .frame(minWidth: 220, idealWidth: LFChrome.sidebarIdeal, maxWidth: 300)
                    .background(LFChrome.sidebar)
                Rectangle()
                    .fill(LFChrome.hairline)
                    .frame(width: 1)
            }
            detail
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .background(LFChrome.canvas)
        }
        .background(LFChrome.canvas)
        .animation(LFChrome.motion, value: sidebarVisible)
    }
}
