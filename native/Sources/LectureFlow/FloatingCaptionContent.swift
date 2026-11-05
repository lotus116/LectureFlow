import LectureFlowCore
import SwiftUI

enum FloatingCaptionAnchor: Hashable { case tail }

/// At most six visible paragraphs. Keep the empty state outside the scrolling tree:
/// its former shared "latest" identity could leave the waiting text as a stale footer.
struct FloatingCaptionContent<Row: View>: View {
    let captions: [LiveCaption]
    let waiting: Bool
    let fontSize: Double
    let spacing: CGFloat
    @ViewBuilder let row: (LiveCaption) -> Row

    var body: some View {
        if captions.isEmpty {
            Text(waiting ? "等待讲话…" : "字幕将显示在这里")
                .font(.system(size: fontSize, weight: .medium))
                .foregroundStyle(.white.opacity(0.75))
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("floating-empty-state")
        } else {
            ScrollView {
                VStack(alignment: .leading, spacing: spacing) {
                    ForEach(captions) { caption in
                        row(caption).id(caption.id)
                    }
                    Color.clear.frame(height: 1).id(FloatingCaptionAnchor.tail)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}
