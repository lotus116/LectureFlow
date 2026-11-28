import SwiftUI

/// Local backing for readable subtitle text and controls. Outer window opacity
/// remains user-adjustable; the worst case is old English over a white slide.
enum LFFloatingContrast {
    static let backingOpacity = 0.86
    static let olderTextOpacity = 0.72
    static let englishOpacity = 0.85
}

// `forest` remains the stored identifier for existing users; its light palette is now Amber.
enum LFTheme: String, CaseIterable, Identifiable {
    case ice, forest
    static let preferenceKey = "colorTheme"
    var id: String { rawValue }
    var title: String { self == .ice ? "冰蓝（默认）" : "琥珀" }
    init(preference: String?) { self = LFTheme(rawValue: preference ?? "") ?? .ice }
}

/// Each consumer observes the preference, so switching never recreates a view's
/// identity, resets a reading position, or touches the capture/session state.
@propertyWrapper struct ThemePalette: DynamicProperty {
    @AppStorage(LFTheme.preferenceKey) private var selection = LFTheme.ice.rawValue
    @Environment(\.colorScheme) private var scheme
    @Environment(\.colorSchemeContrast) private var contrast
    var wrappedValue: LFPalette {
        LFPalette(theme: LFTheme(preference: selection), dark: scheme == .dark,
                  increasedContrast: contrast == .increased)
    }
}

struct LFPalette {
    let theme: LFTheme
    let dark: Bool
    var increasedContrast = false

    enum Role: CaseIterable {
        case accent, accentInk, ink, canvas, sidebar, panel, selection, recorder
        case recordSignal, saved, translation, floating
        case warningInk, dangerInk, diffRemoved, diffAdded
    }

    func hex(_ role: Role) -> UInt32 {
        // Keep the complete legacy ice palette when no preference is saved.
        let ice: [Role: (UInt32, UInt32)] = [
            .warningInk: (0x805412, 0xF3CB7A), .dangerInk: (0xAA273A, 0xFFADB5),
            .diffRemoved: (0x9B2539, 0xFFADB5), .diffAdded: (0x205D3D, 0xA1DFB2),
            .accent: (0x2563EB, 0x3671DB), .accentInk: (0x1D4ED8, 0xB5D6FF),
            .ink: (0x17345C, 0xE5EEFF), .canvas: (0xF8FBFF, 0x101827),
            .sidebar: (0xEEF5FC, 0x0C1422), .panel: (0xFFFFFF, 0x172438),
            .selection: (0xDCEBFF, 0x203D66), .recorder: (0xEFF5FD, 0x192B43),
            .recordSignal: (0xE64D31, 0xFF8B69), .saved: (0x28724F, 0x92D3AC),
            .translation: (0x43546A, 0xC3D0E0), .floating: (0x0F141F, 0x0F141F)
        ]
        let forest: [Role: (UInt32, UInt32)] = [
            .warningInk: (0x805412, 0xF3CB7A), .dangerInk: (0xAA273A, 0xFFADB5),
            .diffRemoved: (0x9B2539, 0xFFADB5), .diffAdded: (0x205D3D, 0xA1DFB2),
            .accent: (0xB45309, 0x38785F), .accentInk: (0x92400E, 0xACDEC3),
            .ink: (0x1C1410, 0xE6EDE5), .canvas: (0xFAF7F4, 0x171D19),
            .sidebar: (0xEDE7DF, 0x121A15), .panel: (0xF4EFE8, 0x202A23),
            .selection: (0xFAECD4, 0x2B4A38), .recorder: (0xFFF5E8, 0x28362B),
            .recordSignal: (0xE64D31, 0xFF8B69), .saved: (0x28724F, 0x92D3AC),
            .translation: (0x78614F, 0xC7D4C6), .floating: (0x141D17, 0x141D17)
        ]
        if increasedContrast {
            switch role {
            case .ink, .translation: return dark ? 0xFFFFFF : 0x111A16
            case .accentInk: return dark ? 0xFFFFFF : (theme == .ice ? 0x143A9B : 0x123C2A)
            default: break
            }
        }
        let pair = (theme == .ice ? ice : forest)[role]!
        return dark ? pair.1 : pair.0
    }
    private func color(_ role: Role) -> Color {
        let value = hex(role)
        return Color(.sRGB, red: Double((value >> 16) & 255) / 255,
                     green: Double((value >> 8) & 255) / 255,
                     blue: Double(value & 255) / 255, opacity: 1)
    }
    var bodyText: Color { theme == .forest && !dark ? ink : .primary }
    var secondaryText: Color { theme == .forest && !dark ? transcriptTranslation : .secondary }
    var warningInk: Color { color(.warningInk) }
    var dangerInk: Color { color(.dangerInk) }
    var diffRemoved: Color { color(.diffRemoved) }
    var diffAdded: Color { color(.diffAdded) }
    var accent: Color { color(.accent) }
    var accentInk: Color { color(.accentInk) }
    var ink: Color { color(.ink) }
    var canvas: Color { color(.canvas) }
    var sidebar: Color { color(.sidebar) }
    var panel: Color { color(.panel) }
    var selection: Color { color(.selection) }
    var recorder: Color { color(.recorder) }
    var recordSignal: Color { color(.recordSignal) }
    var saved: Color { color(.saved) }
    var transcriptTranslation: Color { color(.translation) }
    var floating: Color {
        theme == .ice ? Color(red: 0.06, green: 0.08, blue: 0.12) : color(.floating)
    }
    // Marked buttons contain small text as well as an icon. System blue is only
    // ~3.52:1 on a white panel; the existing theme ink satisfies text contrast.
    var bookmark: Color { accentInk }
    var dropHighlight: Color { accent.opacity(0.14) }
    var dropStroke: Color { accent.opacity(0.55) }
    var hairline: Color { Color.primary.opacity(increasedContrast ? 0.5 : 0.07) }
    var rowHover: Color {
        if theme == .forest && !dark && !increasedContrast {
            return Color(.sRGB, red: 245.0 / 255, green: 238.0 / 255, blue: 228.0 / 255)
        }
        return accent.opacity(increasedContrast ? 0.18 : 0.08)
    }
    var iconWell: Color { accent.opacity(0.12) }
    var softStroke: Color { hairline }

    func softPanel() -> some View {
        RoundedRectangle(cornerRadius: LFChrome.radiusM, style: .continuous)
            .fill(panel)
            .overlay(RoundedRectangle(cornerRadius: LFChrome.radiusM, style: .continuous)
                .stroke(hairline, lineWidth: 1))
    }
    func iceMist() -> some View {
        LinearGradient(colors: [accent.opacity(0.025), .clear], startPoint: .topLeading,
                       endPoint: .bottomTrailing)
    }
}
