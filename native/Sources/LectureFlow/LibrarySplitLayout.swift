import SwiftUI

/// The system NavigationSplitView inserts its own movable titlebar toggle.
/// This layout owns its sidebar so the single explicit toolbar control stays fixed.
struct LibrarySplitLayout<Sidebar: View, Detail: View>: View {
    @Binding var sidebarVisible: Bool
    let sidebar: Sidebar
    let detail: Detail
    init(sidebarVisible: Binding<Bool>, @ViewBuilder sidebar: () -> Sidebar,
         @ViewBuilder detail: () -> Detail) {
        _sidebarVisible = sidebarVisible; self.sidebar = sidebar(); self.detail = detail()
    }
    var body: some View {
        HStack(spacing: 0) {
            if sidebarVisible {
                sidebar.frame(width: 225)
                Divider()
            }
            detail.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}
