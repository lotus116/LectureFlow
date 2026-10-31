import SwiftUI

/// Shared visual tokens for a quiet macOS study workspace.
/// Ice-blue accent only; surfaces stay flat; no dashboard chrome.
enum LFChrome {
    /// Cirno ice-blue — sparse accent, not a theme wash.
    static let accent = Color(red: 0.16, green: 0.44, blue: 0.62)

    static let space4: CGFloat = 4
    static let space8: CGFloat = 8
    static let space12: CGFloat = 12
    static let space16: CGFloat = 16
    static let space24: CGFloat = 24
    static let space32: CGFloat = 32

    static let radiusS: CGFloat = 6
    static let radiusM: CGFloat = 10
    static let radiusL: CGFloat = 14

    static let contentInset: CGFloat = 24
    static let sidebarIdeal: CGFloat = 248

    static let titleSize: CGFloat = 24
    static let sectionSize: CGFloat = 13
    static let bodySize: CGFloat = 14
    static let captionSize: CGFloat = 11
    static let transcriptEN: CGFloat = 15
    static let transcriptZH: CGFloat = 17

    static let dropHighlight = accent.opacity(0.12)
    static let dropStroke = accent.opacity(0.5)
    static let hairline = Color.primary.opacity(0.08)
    static let rowHover = Color.primary.opacity(0.04)

    /// Soft panel only where a control cluster needs grouping (session bar).
    static func softPanel() -> some View {
        RoundedRectangle(cornerRadius: radiusM, style: .continuous)
            .fill(Color(nsColor: .controlBackgroundColor))
            .overlay(
                RoundedRectangle(cornerRadius: radiusM, style: .continuous)
                    .strokeBorder(hairline, lineWidth: 1)
            )
    }

    /// Deprecated heavy card look — kept as alias for gradual migration.
    static let cardRadius = radiusM
    static let cardFill = Color.primary.opacity(0.03)
    static let softStroke = hairline
    static func cardBackground() -> some View { softPanel() }

    static let motion = Animation.easeInOut(duration: 0.16)
}

struct EmptyPanel: View {
    let title: String
    let systemImage: String
    let description: String
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        ContentUnavailableView {
            Label(title, systemImage: systemImage)
                .symbolRenderingMode(.hierarchical)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.secondary)
        } description: {
            Text(description)
                .font(.system(size: LFChrome.bodySize))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 300)
        } actions: {
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .buttonStyle(.borderedProminent)
                    .tint(LFChrome.accent)
                    .controlSize(.regular)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// Flat list row for audio / lecture lists — no per-row card chrome.
struct LFListRow<Leading: View, Trailing: View>: View {
    let title: String
    var subtitle: String? = nil
    @ViewBuilder var leading: () -> Leading
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(spacing: LFChrome.space12) {
            leading()
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.system(size: LFChrome.bodySize, weight: .medium))
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(title)
                if let subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.system(size: LFChrome.captionSize))
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            trailing()
        }
        .padding(.vertical, LFChrome.space8)
        .padding(.horizontal, LFChrome.space4)
        .contentShape(Rectangle())
    }
}

struct LFSectionLabel: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: LFChrome.sectionSize, weight: .semibold))
            .foregroundStyle(.secondary)
            .textCase(nil)
    }
}

extension View {
    func lfSoftPanelPadding() -> some View {
        padding(.horizontal, LFChrome.space16)
            .padding(.vertical, LFChrome.space12)
            .background(LFChrome.softPanel())
    }

    /// Prefer soft panel; keep old name for call sites.
    func lfCardPadding() -> some View { lfSoftPanelPadding() }

    func lfContentPadding() -> some View {
        padding(LFChrome.contentInset)
    }
}
