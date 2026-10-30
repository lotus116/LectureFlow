import SwiftUI

/// Lightweight visual tokens. Keep behavior unchanged; only presentation.
enum LFChrome {
    static let accent = Color(red: 0.16, green: 0.44, blue: 0.62)
    static let cardRadius: CGFloat = 12
    static let cardFill = Color.primary.opacity(0.035)
    static let softStroke = Color.primary.opacity(0.06)
    static let dropHighlight = Color.accentColor.opacity(0.14)
    static let dropStroke = Color.accentColor.opacity(0.55)

    static func cardBackground() -> some View {
        RoundedRectangle(cornerRadius: cardRadius, style: .continuous)
            .fill(cardFill)
            .overlay(
                RoundedRectangle(cornerRadius: cardRadius, style: .continuous)
                    .strokeBorder(softStroke, lineWidth: 1)
            )
    }
}

/// Centers empty-state art inside whatever space the parent already allocated.
struct EmptyPanel: View {
    let title: String
    let systemImage: String
    let description: String

    var body: some View {
        ContentUnavailableView {
            Label(title, systemImage: systemImage)
                .symbolRenderingMode(.hierarchical)
                .font(.title2.weight(.semibold))
        } description: {
            Text(description)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

extension View {
    func lfCardPadding() -> some View {
        padding(14).background(LFChrome.cardBackground())
    }
}
