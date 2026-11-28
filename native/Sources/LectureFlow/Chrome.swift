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

    static let sheetTitleSize: CGFloat = 20
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

/// Centers only the caller's available content region, with scrolling when
/// intrinsic content exceeds it. Toolbars belong outside this container.
struct LFCenteredContent<Content: View>: View {
    @ViewBuilder var content: (CGSize) -> Content
    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                content(geometry.size)
                    .padding(16).accessibilityIdentifier("empty-content")
                    .frame(maxWidth: .infinity)
                    .frame(minHeight: geometry.size.height, alignment: .center)
            }.accessibilityIdentifier("empty-region")
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct EmptyPanel: View {
    @ThemePalette private var palette
    let title: String
    let systemImage: String
    let description: String
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil
    var useAppIcon = false

    var body: some View {
        LFCenteredContent { size in
            if size.height < 210 {
                HStack(spacing: 12) {
                    Image(systemName: systemImage).font(.system(size: 24))
                        .foregroundStyle(palette.accentInk).frame(width: 36)
                    VStack(alignment: .leading, spacing: 5) {
                        Text(title).font(.system(size: 15, weight: .semibold))
                        Text(description).font(.system(size: 12)).lineSpacing(2)
                            .foregroundStyle(palette.secondaryText)
                            .fixedSize(horizontal: false, vertical: true)
                        actions
                    }
                }.frame(maxWidth: 520)
            } else {
                VStack(spacing: 12) {
                    if useAppIcon {
                        Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 56, height: 56)
                            .clipShape(RoundedRectangle(cornerRadius: LFChrome.radiusM))
                    } else {
                        Image(systemName: systemImage).font(.system(size: 28, weight: .medium))
                            .foregroundStyle(palette.accentInk).frame(width: 52, height: 52)
                            .background(palette.iconWell, in: RoundedRectangle(cornerRadius: LFChrome.radiusM))
                    }
                    Text(title).font(.system(size: 17, weight: .semibold))
                    Text(description).font(.system(size: LFChrome.bodySize)).lineSpacing(2)
                        .foregroundStyle(palette.secondaryText).multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                    actions
                }.frame(maxWidth: 360)
            }
        }
    }
    @ViewBuilder private var actions: some View {
        if let actionTitle, let action {
            Button(actionTitle, action: action).buttonStyle(.borderedProminent).tint(palette.accent)
        }
    }
}

/// Lecture / audio row with ice icon well — flat, not a card.
struct LFListRow<Leading: View, Trailing: View>: View {
    @ThemePalette private var palette
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
                    .foregroundStyle(palette.bodyText)
                    .lineLimit(2)
                    .truncationMode(.tail)
                    .help(title)
                if let subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.system(size: LFChrome.captionSize))
                        .foregroundStyle(palette.secondaryText)
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
    @ThemePalette private var palette
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: LFChrome.sectionSize, weight: .semibold))
            .foregroundStyle(palette.secondaryText)
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

/// Shared native sheet rhythm: a quiet header, flexible content, and a separated action edge.
extension View {
    func lfSheetHeader() -> some View {
        padding(.bottom, 12).overlay(alignment: .bottom) { Divider() }
    }
    func lfSheetFooter() -> some View {
        padding(.top, 12).overlay(alignment: .top) { Divider() }
    }
}
