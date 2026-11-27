import XCTest
import AppKit
@testable import LectureFlow

final class ThemeTests: XCTestCase {
    func testMissingOrUnknownPreferenceUsesIceWithoutMigration() {
        XCTAssertEqual(LFTheme(preference: nil), .ice)
        XCTAssertEqual(LFTheme(preference: "future-theme"), .ice)
        XCTAssertEqual(LFTheme(preference: "forest"), .forest)
        let suite = "LectureFlow.ThemeTests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        _ = LFTheme(preference: defaults.string(forKey: LFTheme.preferenceKey))
        XCTAssertNil(defaults.object(forKey: LFTheme.preferenceKey))
    }

    func testIceRetainsExistingSurfaceColors() {
        let light = LFPalette(theme: .ice, dark: false)
        let dark = LFPalette(theme: .ice, dark: true)
        XCTAssertEqual(light.hex(.canvas), 0xF8FBFF)
        XCTAssertEqual(light.hex(.sidebar), 0xEEF5FC)
        XCTAssertEqual(light.hex(.panel), 0xFFFFFF)
        XCTAssertEqual(light.hex(.accent), 0x2563EB)
        XCTAssertEqual(dark.hex(.canvas), 0x101827)
        XCTAssertEqual(dark.hex(.panel), 0x172438)
    }

    func testReadingContrastAcrossThemesAndSystemAppearances() {
        for theme in LFTheme.allCases {
            for dark in [false, true] {
                for increased in [false, true] {
                    let palette = LFPalette(theme: theme, dark: dark, increasedContrast: increased)
                    for text in [LFPalette.Role.ink, .translation, .accentInk] {
                        for background in [LFPalette.Role.panel, .canvas, .sidebar, .recorder, .selection] {
                            XCTAssertGreaterThanOrEqual(ratio(palette.hex(text), palette.hex(background)), 4.5,
                                "\(theme) dark=\(dark) increased=\(increased): \(text) on \(background)")
                        }
                    }
                    XCTAssertGreaterThanOrEqual(ratio(0xFFFFFF, palette.hex(.accent)), 4.5)
                }
            }
        }
    }

    func testRecordingSignalDoesNotChangeMeaningWithTheme() {
        for dark in [false, true] {
            XCTAssertEqual(LFPalette(theme: .ice, dark: dark).hex(.recordSignal),
                           LFPalette(theme: .forest, dark: dark).hex(.recordSignal))
        }
    }

    func testMarkedBookmarkTextContrast() throws {
        for theme in LFTheme.allCases {
            let palette = LFPalette(theme: theme, dark: false)
            let color = try XCTUnwrap(NSColor(palette.bookmark).usingColorSpace(.sRGB))
            let hex = UInt32((color.redComponent * 255).rounded()) << 16
                | UInt32((color.greenComponent * 255).rounded()) << 8
                | UInt32((color.blueComponent * 255).rounded())
            let contrast = ratio(hex, palette.hex(.panel))
            print("BOOKMARK \(theme): \(String(hex, radix: 16)) contrast \(contrast)")
            XCTAssertGreaterThanOrEqual(contrast, 4.5, "Small marked-button text must remain readable")
            XCTAssertGreaterThanOrEqual(ratio(palette.hex(.recordSignal), palette.hex(.recorder)), 3)
        }
    }

    private func ratio(_ a: UInt32, _ b: UInt32) -> Double {
        func luminance(_ hex: UInt32) -> Double {
            let channels = [16, 8, 0].map { shift -> Double in
                let c = Double((hex >> shift) & 255) / 255
                return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4)
            }
            return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
        }
        let x = luminance(a), y = luminance(b)
        return (max(x, y) + 0.05) / (min(x, y) + 0.05)
    }

    func testFloatingOldEnglishOnWhiteAtMinimumWindowOpacity() throws {
        func luminance(_ channels: [Double]) -> Double {
            zip(channels, [0.2126, 0.7152, 0.0722]).reduce(0) { result, item in
                let value = item.0
                return result + item.1 * (value <= 0.04045 ? value / 12.92 : pow((value + 0.055) / 1.055, 2.4))
            }
        }
        for theme in LFTheme.allCases {
            let color = try XCTUnwrap(NSColor(LFPalette(theme: theme, dark: true).floating).usingColorSpace(.sRGB))
            let channels = [color.redComponent, color.greenComponent, color.blueComponent]
            let alpha = 1 - (1 - 0.2) * (1 - LFFloatingContrast.backingOpacity)
            let background = channels.map { 1 + ($0 - 1) * alpha }
            let textAlpha = LFFloatingContrast.olderTextOpacity * LFFloatingContrast.englishOpacity
            let foreground = background.map { $0 + (1 - $0) * textAlpha }
            let contrast = (luminance(foreground) + 0.05) / (luminance(background) + 0.05)
            print("FLOAT computed white-slide contrast \(theme): \(contrast)")
            XCTAssertGreaterThanOrEqual(contrast, 4.5, "Old bilingual English must remain readable at minimum opacity")
        }
    }
}
