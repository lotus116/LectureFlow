import SwiftUI

/// Presentation severity only; the original message and action stay authoritative.
enum LFStatusTone {
    case information, working, attention, failure
    var symbol: String {
        switch self {
        case .information: "info.circle"
        case .working: "arrow.triangle.2.circlepath"
        case .attention: "exclamationmark.triangle"
        case .failure: "exclamationmark.octagon.fill"
        }
    }
}

struct LFStatusBanner<Actions: View>: View {
    @ThemePalette private var palette
    let message: String
    let tone: LFStatusTone
    @ViewBuilder var actions: () -> Actions
    private var signal: Color {
        switch tone {
        case .information, .working: palette.accentInk
        case .attention: palette.warningInk
        case .failure: palette.dangerInk
        }
    }
    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: 12) {
                messageLabel
                Spacer(minLength: 0)
                actions().fixedSize(horizontal: true, vertical: false)
            }
            VStack(alignment: .leading, spacing: 8) {
                messageLabel
                actions().frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
        .font(.system(size: 13))
        .controlSize(.small)
        .padding(.horizontal, 10).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 8))
        .overlay(alignment: .leading) {
            if tone == .attention || tone == .failure {
                RoundedRectangle(cornerRadius: 1.5).fill(signal.opacity(0.75)).frame(width: 3)
            }
        }
    }
    private var messageLabel: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: tone.symbol).foregroundStyle(signal).accessibilityHidden(true)
            Text(message).foregroundStyle(palette.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
extension LFStatusBanner where Actions == EmptyView {
    init(_ message: String, tone: LFStatusTone = .information) {
        self.message = message; self.tone = tone; self.actions = { EmptyView() }
    }
}

enum LFReviewChangeKind {
    case removed, added, unchanged
    init(_ change: String) {
        self = change.hasPrefix("- ") ? .removed : (change.hasPrefix("+ ") ? .added : .unchanged)
    }
    func accessibleDescription(_ change: String) -> String {
        switch self {
        case .removed: "删除，" + String(change.dropFirst(2))
        case .added: "新增，" + String(change.dropFirst(2))
        case .unchanged: change
        }
    }
}
struct LFReviewChange: View {
    @ThemePalette private var palette
    let change: String
    private var kind: LFReviewChangeKind { LFReviewChangeKind(change) }
    private var ink: Color {
        switch kind {
        case .removed: palette.diffRemoved
        case .added: palette.diffAdded
        case .unchanged: palette.ink
        }
    }
    var body: some View {
        Text(change).font(.system(size: 13, design: .monospaced))
            .foregroundStyle(ink).lineSpacing(3)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 8).padding(.vertical, 5)
            .background(ink.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))
            .accessibilityLabel(kind.accessibleDescription(change))
    }
}
