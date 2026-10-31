import AppKit
import SwiftUI

/// Cirno ice-blue accents over neutral reading surfaces, with a separate dark palette.
enum LFChrome {
    private static func adaptive(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let hex = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: Double((hex >> 16) & 255) / 255,
                           green: Double((hex >> 8) & 255) / 255,
                           blue: Double(hex & 255) / 255, alpha: 1)
        })
    }
    static let accent = adaptive(0x2563EB, 0x3671DB)
    static let accentInk = adaptive(0x1D4ED8, 0xB5D6FF)
    static let ink = adaptive(0x17345C, 0xE5EEFF)
    static let canvas = adaptive(0xF8FBFF, 0x101827)
    static let sidebar = adaptive(0xEEF5FC, 0x0C1422)
    static let panel = adaptive(0xFFFFFF, 0x172438)
    static let selection = adaptive(0xDCEBFF, 0x203D66)
    static let recorder = adaptive(0xEFF5FD, 0x192B43)
    static let recordSignal = adaptive(0xE64D31, 0xFF8B69)
    static let saved = adaptive(0x28724F, 0x92D3AC)

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

    static let dropHighlight = accent.opacity(0.14)
    static let dropStroke = accent.opacity(0.55)
    static let hairline = Color.primary.opacity(0.07)
    static let rowHover = accent.opacity(0.08)
    static let iconWell = accent.opacity(0.12)

    static let cardRadius = radiusM
    static let cardFill = panel
    static let softStroke = hairline

    static let motion = Animation.easeInOut(duration: 0.16)

    /// Flat surface with a subtle edge to distinguish it from the canvas.
    static func softPanel() -> some View {
        RoundedRectangle(cornerRadius: radiusM, style: .continuous)
            .fill(panel)
            .overlay(
                RoundedRectangle(cornerRadius: radiusM, style: .continuous)
                    .stroke(hairline, lineWidth: 1)
            )

    }

    static func cardBackground() -> some View { softPanel() }

    /// Top wash for course / lecture headers — quiet atmosphere, not a landing hero.
    static func iceMist() -> some View {
        LinearGradient(
            colors: [accent.opacity(0.025), .clear],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}

struct EmptyPanel: View {
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
                        .shadow(color: LFChrome.accent.opacity(0.25), radius: 12, y: 4)
                } else {
                    Image(systemName: systemImage)
                        .symbolRenderingMode(.hierarchical)
                        .font(.system(size: 28, weight: .medium))
                        .foregroundStyle(LFChrome.accentInk)
                        .frame(width: 52, height: 52)
                        .background(LFChrome.iconWell, in: RoundedRectangle(cornerRadius: LFChrome.radiusM, style: .continuous))
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
                    .tint(LFChrome.accent)
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
    let systemImage: String
    var body: some View {
        Image(systemName: systemImage)
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(LFChrome.accentInk)
            .frame(width: 28, height: 28)
            .background(LFChrome.iconWell, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
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
    func lfSoftPanelPadding() -> some View {
        padding(.horizontal, LFChrome.space16)
            .padding(.vertical, 10)
            .background(LFChrome.softPanel())
    }

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
