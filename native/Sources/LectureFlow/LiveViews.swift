import AppKit
import LectureFlowCore
import SwiftUI

struct LiveControlView: View {
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    let lecture: Lecture
    let showingAudio: Bool
    var body: some View { SessionControlBar(workspace: workspace, live: live, showingAudio: showingAudio) }
}

struct LiveTranscriptView: View {
    @ObservedObject var live: LiveController
    let lecture: Lecture
    let backend: Backend
    @AppStorage("transcriptTextSize") private var textSize = 15.0
    @State private var search = ""
    @State private var language = "both"
    @State private var follow = true
    private var visible: [LiveCaption] {
        live.captions.filter { search.isEmpty || $0.english.localizedCaseInsensitiveContains(search) || $0.chinese.contains(search) }
    }
    private var updateKey: String { live.captions.last.map { "\($0.id):\($0.revision)" } ?? "" }
    private var enSize: CGFloat { LFChrome.transcriptEnglish(textSize) }
    private var zhSize: CGFloat { LFChrome.transcriptChinese(textSize) }

    var body: some View {
        ScrollViewReader { proxy in
            VStack(alignment: .leading, spacing: 12) {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) {
                        transcriptFilters
                        Spacer(minLength: 8)
                        transcriptActions(proxy: proxy)
                    }
                    .frame(minWidth: 460)

                    VStack(alignment: .leading, spacing: 8) {
                        transcriptFilters
                        transcriptActions(proxy: proxy)
                            .frame(maxWidth: .infinity, alignment: .trailing)
                    }
                }
                Text(follow ? "跟随最新" : "已停止跟随 · 点“返回最新”继续")
                    .font(.system(size: LFChrome.captionSize))
                    .foregroundStyle(.secondary)
                if live.captions.isEmpty {
                    EmptyPanel(
                        title: "等待讲话",
                        systemImage: "waveform",
                        description: "开启字幕后，实时内容会出现在这里。"
                    )
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: LFChrome.space16) {
                            ForEach(visible) { caption in
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(clock(caption.start))
                                        .font(.system(size: max(11, LFChrome.captionSize * (textSize / 15)), weight: .medium).monospacedDigit())
                                        .foregroundStyle(LFChrome.accent.opacity(0.85))
                                    // Main window: English above, Chinese below.
                                    if language != "zh", !caption.english.isEmpty {
                                        Text(caption.english)
                                            .font(.system(size: enSize))
                                            .lineSpacing(max(3, enSize * 0.28))
                                    }
                                    if language != "en", !caption.chinese.isEmpty {
                                        Text(caption.chinese)
                                            .font(.system(size: zhSize))
                                            .foregroundStyle(.secondary)
                                            .lineSpacing(max(3, zhSize * 0.28))
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .textSelection(.enabled)
                                .id(caption.id)
                                .padding(.bottom, LFChrome.space4)
                                Rectangle()
                                    .fill(LFChrome.hairline)
                                    .frame(height: 1)
                            }
                            Color.clear.frame(height: 1).id("latest-caption")
                        }
                        .lfReadingColumn()
                        .padding(.vertical, LFChrome.space8)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(ScrollActivityObserver { follow = false })
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .onChange(of: updateKey) { _, _ in
                if follow && search.isEmpty { proxy.scrollTo("latest-caption", anchor: .bottom) }
            }
            .onChange(of: search) { _, text in if !text.isEmpty { follow = false } }
        }
    }
    private var transcriptFilters: some View {
        HStack(spacing: 8) {
            Picker("显示", selection: $language) {
                Text("双语").tag("both"); Text("中文").tag("zh"); Text("英文").tag("en")
            }
            .frame(width: 115)
            .layoutPriority(1)
            TextField("搜索字幕", text: $search)
                .textFieldStyle(.roundedBorder)
                .frame(minWidth: 56, maxWidth: 160)
                .layoutPriority(0)
            TranscriptFontControls(size: $textSize)
        }
    }

    private func transcriptActions(proxy: ScrollViewProxy) -> some View {
        HStack(spacing: 8) {
            Button("复制") {
                let text = visible.map { caption in
                    [clock(caption.start), language == "en" ? "" : caption.chinese,
                     language == "zh" ? "" : caption.english].filter { !$0.isEmpty }.joined(separator: "\n")
                }.joined(separator: "\n\n")
                NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string)
            }
            .disabled(visible.isEmpty)
            Button("导出实时字幕") {
                Task {
                    do {
                        let data = try await backend.call(["live", "export", lecture.id])
                        let result = try JSONDecoder().decode(Exported.self, from: data)
                        NSWorkspace.shared.open(URL(fileURLWithPath: result.directory))
                    } catch { live.error = error.localizedDescription }
                }
            }
            .disabled(live.captions.isEmpty)
            Button("返回最新") {
                follow = true; search = ""
                DispatchQueue.main.async { withAnimation { proxy.scrollTo("latest-caption", anchor: .bottom) } }
            }
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    private func clock(_ seconds: Double) -> String {
        let time = Int(seconds)
        return String(format: "%02d:%02d:%02d", time / 3600, time / 60 % 60, time % 60)
    }
    private func label(_ status: String) -> String {
        switch status {
        case "temporary": return "临时"
        case "stable": return "稳定"
        case "revised": return "已修订"
        default: return "已锁定"
        }
    }
}

/// Shared A/A controls for live and offline transcript reading size.
struct TranscriptFontControls: View {
    @Binding var size: Double
    private let range: ClosedRange<Double> = 12...28

    var body: some View {
        HStack(spacing: 2) {
            Button {
                size = max(range.lowerBound, size - 1)
            } label: {
                Image(systemName: "textformat.size.smaller")
            }
            .disabled(size <= range.lowerBound)
            .help("减小转写字号")

            Button {
                size = min(range.upperBound, size + 1)
            } label: {
                Image(systemName: "textformat.size.larger")
            }
            .disabled(size >= range.upperBound)
            .help("增大转写字号")
        }
        .buttonStyle(.borderless)
        .controlSize(.small)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("转写字号")
        .accessibilityValue("\(Int(size))")
    }
}

private struct ScrollActivityObserver: NSViewRepresentable {
    let action: () -> Void
    func makeNSView(context: Context) -> ActivityView { let view = ActivityView(); view.action = action; return view }
    func updateNSView(_ view: ActivityView, context: Context) { view.action = action }
    final class ActivityView: NSView {
        var action: (() -> Void)?
        private var monitor: Any?
        override func viewDidMoveToWindow() {
            if let monitor { NSEvent.removeMonitor(monitor); self.monitor = nil }
            guard window != nil else { return }
            monitor = NSEvent.addLocalMonitorForEvents(matching: [.scrollWheel, .leftMouseDown, .keyDown]) { [weak self] event in
                guard let self, event.window == self.window else { return event }
                if event.type == .keyDown {
                    if [115, 116, 119, 121].contains(Int(event.keyCode)) { self.action?() }
                } else if self.bounds.contains(self.convert(event.locationInWindow, from: nil)) { self.action?() }
                return event
            }
        }
        deinit { if let monitor { NSEvent.removeMonitor(monitor) } }
    }
}
