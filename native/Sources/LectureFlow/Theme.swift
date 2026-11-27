import SwiftUI

/// Local backing for readable subtitle text and controls. Outer window opacity
/// remains user-adjustable; the worst case is old English over a white slide.
enum LFFloatingContrast {
    static let backingOpacity = 0.86
    static let olderTextOpacity = 0.72
    static let englishOpacity = 0.85
}

enum LFTheme: String, CaseIterable, Identifiable {
    case ice, forest
    static let preferenceKey = "colorTheme"
    var id: String { rawValue }
    var title: String { self == .ice ? "冰蓝（默认）" : "暖石白 · 墨绿" }
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
    }

    func hex(_ role: Role) -> UInt32 {
        // Keep the complete legacy ice palette when no preference is saved.
        let ice: [Role: (UInt32, UInt32)] = [
            .accent: (0x2563EB, 0x3671DB), .accentInk: (0x1D4ED8, 0xB5D6FF),
            .ink: (0x17345C, 0xE5EEFF), .canvas: (0xF8FBFF, 0x101827),
            .sidebar: (0xEEF5FC, 0x0C1422), .panel: (0xFFFFFF, 0x172438),
            .selection: (0xDCEBFF, 0x203D66), .recorder: (0xEFF5FD, 0x192B43),
            .recordSignal: (0xE64D31, 0xFF8B69), .saved: (0x28724F, 0x92D3AC),
            .translation: (0x43546A, 0xC3D0E0), .floating: (0x0F141F, 0x0F141F)
        ]
        let forest: [Role: (UInt32, UInt32)] = [
            .accent: (0x24634E, 0x38785F), .accentInk: (0x205840, 0xACDEC3),
            .ink: (0x283D32, 0xE6EDE5), .canvas: (0xF8F6F0, 0x171D19),
            .sidebar: (0xEBEEE5, 0x121A15), .panel: (0xFFFEFA, 0x202A23),
            .selection: (0xD9E8DC, 0x2B4A38), .recorder: (0xEFF2E8, 0x28362B),
            .recordSignal: (0xE64D31, 0xFF8B69), .saved: (0x28724F, 0x92D3AC),
            .translation: (0x4B5B4D, 0xC7D4C6), .floating: (0x141D17, 0x141D17)
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
    var rowHover: Color { accent.opacity(increasedContrast ? 0.18 : 0.08) }
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
