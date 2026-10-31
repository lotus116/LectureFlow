import AppKit
import SwiftUI

/// Warm, restrained study workspace with adaptive colors.
enum LFChrome {
    static let accent = Color(nsColor: NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            ? NSColor(calibratedRed: 0.62, green: 0.77, blue: 0.67, alpha: 1)
            : NSColor(calibratedRed: 0.25, green: 0.43, blue: 0.33, alpha: 1)
    })

    static let canvas = Color(nsColor: NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            ? NSColor(calibratedRed: 0.10, green: 0.12, blue: 0.11, alpha: 1)
            : NSColor(calibratedRed: 0.98, green: 0.973, blue: 0.953, alpha: 1)
    })

    static let sidebar = Color(nsColor: NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            ? NSColor(calibratedRed: 0.08, green: 0.10, blue: 0.09, alpha: 1)
            : NSColor(calibratedRed: 0.94, green: 0.93, blue: 0.90, alpha: 1)
    })

    static let panel = Color(nsColor: NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            ? NSColor(calibratedRed: 0.15, green: 0.17, blue: 0.16, alpha: 1)
            : NSColor(calibratedRed: 1.0, green: 0.997, blue: 0.99, alpha: 1)
    })

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
    static let sidebarIdeal: CGFloat = 252
    static let readingMeasure: CGFloat = 680

    static let titleSize: CGFloat = 23
    static let sectionSize: CGFloat = 13
    static let bodySize: CGFloat = 14
    static let captionSize: CGFloat = 12
    static let transcriptEN: CGFloat = 15
    static let transcriptZH: CGFloat = 17

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
            .shadow(color: .black.opacity(0.025), radius: 5, y: 2)
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
                        .foregroundStyle(LFChrome.accent)
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
            .foregroundStyle(LFChrome.accent)
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
        padding(LFChrome.contentInset)
    }

    func lfReadingColumn() -> some View {
        frame(maxWidth: LFChrome.readingMeasure, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
