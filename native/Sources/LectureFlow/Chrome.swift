import AppKit
import SwiftUI

/// Shared geometry and typography; semantic colors live in Theme.swift.
enum LFChrome {
    static let motion = Animation.easeInOut(duration: 0.16)
    static let space4: CGFloat = 4
    static let space8: CGFloat = 8
    static let space10: CGFloat = 10
    static let space12: CGFloat = 12
    static let space16: CGFloat = 16
    static let space20: CGFloat = 20
    static let space24: CGFloat = 24
    static let space32: CGFloat = 32

    static let radiusS: CGFloat = 8
    static let radiusM: CGFloat = 12
    static let radiusL: CGFloat = 16

    static let contentInset: CGFloat = 24
    static let contentInsetCompact: CGFloat = 14
    static let sidebarIdeal: CGFloat = 220
    static let sidebarCompact: CGFloat = 168
    static let windowWidth: CGFloat = 1080
    static let windowHeight: CGFloat = 700
    static let windowMinWidth: CGFloat = 740
    static let windowMinHeight: CGFloat = 520
    static let readingMeasure: CGFloat = 680

    static let titleSize: CGFloat = 23
    static let sectionSize: CGFloat = 13
    static let bodySize: CGFloat = 14
    static let captionSize: CGFloat = 12
    static let transcriptEN: CGFloat = 15
    static let transcriptZH: CGFloat = 17

    /// English/Chinese sizes from the user-adjustable transcript base size.
    static func transcriptEnglish(_ base: Double) -> CGFloat { CGFloat(base) }
    static func transcriptChinese(_ base: Double) -> CGFloat { CGFloat(base) * (transcriptZH / transcriptEN) }
}

struct EmptyPanel: View {
    @ThemePalette private var palette
    let title: String
    let systemImage: String
    let description: String
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil
    var useAppIcon: Bool = false

    var body: some View {
        ContentUnavailableView {
            Group {
                if useAppIcon {
                    Image(nsImage: NSApp.applicationIconImage)
                        .resizable()
                        .frame(width: 56, height: 56)
                        .clipShape(RoundedRectangle(cornerRadius: LFChrome.radiusM, style: .continuous))
                        .shadow(color: palette.accent.opacity(0.25), radius: 12, y: 4)
                } else {
                    Image(systemName: systemImage)
                        .symbolRenderingMode(.hierarchical)
                        .font(.system(size: 28, weight: .medium))
                        .foregroundStyle(palette.accentInk)
                        .frame(width: 52, height: 52)
                        .background(palette.iconWell, in: RoundedRectangle(cornerRadius: LFChrome.radiusM, style: .continuous))
                }
            }
            Text(title)
                .font(.system(size: 17, weight: .semibold))
                .padding(.top, LFChrome.space8)
        } description: {
            Text(description)
                .font(.system(size: LFChrome.bodySize))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
        } actions: {
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .buttonStyle(.borderedProminent)
                    .tint(palette.accent)
                    .controlSize(.large)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// Lecture / audio row with ice icon well — flat, not a card.
struct LFListRow<Leading: View, Trailing: View>: View {
    let title: String
    var subtitle: String? = nil
    @ViewBuilder var leading: () -> Leading
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(spacing: LFChrome.space12) {
            leading()
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.system(size: LFChrome.bodySize, weight: .medium))
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                    .truncationMode(.tail)
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
        .padding(.vertical, 9)
        .padding(.horizontal, 2)
        .contentShape(Rectangle())
    }
}

struct LFIconWell: View {
    @ThemePalette private var palette
    let systemImage: String
    var body: some View {
        Image(systemName: systemImage)
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(palette.accentInk)
            .frame(width: 28, height: 28)
            .background(palette.iconWell, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            .accessibilityHidden(true)
    }
}

struct LFSectionLabel: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: LFChrome.sectionSize, weight: .semibold))
            .foregroundStyle(.secondary)
            .tracking(0.2)
            .textCase(nil)
    }
}

extension View {
    func lfSoftPanelPadding() -> some View { modifier(LFPanelPaddingModifier()) }

    func lfCardPadding() -> some View { lfSoftPanelPadding() }

    func lfContentPadding() -> some View {
        modifier(LFContentPaddingModifier())
    }

    func lfReadingColumn() -> some View {
        frame(maxWidth: LFChrome.readingMeasure, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct LFContentPaddingModifier: ViewModifier {
    @Environment(\.lfCompactChrome) private var compact
    func body(content: Content) -> some View {
        content.padding(compact ? LFChrome.contentInsetCompact : LFChrome.contentInset)
    }
}

private struct LFPanelPaddingModifier: ViewModifier {
    @ThemePalette private var palette
    func body(content: Content) -> some View {
        content.padding(.horizontal, LFChrome.space16).padding(.vertical, 10)
            .background(palette.softPanel())
    }
}
