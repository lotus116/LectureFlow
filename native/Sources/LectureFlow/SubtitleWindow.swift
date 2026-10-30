import AppKit
import SwiftUI

@MainActor
final class SubtitleWindow {
    private var panel: NSPanel?
    func show(live: LiveController) {
        if let panel { panel.orderFrontRegardless(); return }
        let window = NSPanel(contentRect: NSRect(x: 260, y: 180, width: 650, height: 260),
            styleMask: [.titled, .closable, .resizable, .utilityWindow, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = "LectureFlow 字幕"
        window.level = .floating
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.hidesOnDeactivate = false
        window.isMovableByWindowBackground = true
        window.isReleasedWhenClosed = false
        window.isOpaque = false
        window.backgroundColor = .clear
        window.titlebarAppearsTransparent = true
        window.contentMinSize = NSSize(width: 380, height: 150)
        window.setFrameAutosaveName("LectureFlowSubtitles")
        window.contentView = NSHostingView(rootView: FloatingSubtitles(live: live))
        panel = window
        window.orderFrontRegardless()
    }
}

private struct FloatingSubtitles: View {
    @ObservedObject var live: LiveController
    @AppStorage("subtitleSize") private var fontSize = 30.0
    @AppStorage("subtitleOpacity") private var opacity = 0.86
    @AppStorage("subtitleEnglish") private var showEnglish = false
    @State private var settings = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Circle().fill(live.flags.needsMicrophone ? Color.teal : Color.gray).frame(width: 6, height: 6)
                Text(live.flags.needsMicrophone ? "课堂字幕" : "已暂停").font(.caption)
                Spacer()
                Toggle("英文", isOn: $showEnglish).toggleStyle(.checkbox).font(.caption)
                Button { settings.toggle() } label: { Image(systemName: "textformat.size") }
                    .buttonStyle(.plain).help("字号与透明度")
                    .popover(isPresented: $settings) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("字号 \(Int(fontSize))"); Slider(value: $fontSize, in: 18...64)
                            Text("背景透明度"); Slider(value: $opacity, in: 0.2...1)
                        }.padding(20).frame(width: 250)
                    }
            }.foregroundStyle(.white.opacity(0.75))
            ScrollView {
                if let caption = live.captions.last {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(primary(caption)).font(.system(size: fontSize, weight: .medium)).lineSpacing(5)
                            .contentTransition(.interpolate)
                        if showEnglish && !caption.chinese.isEmpty && !caption.english.isEmpty {
                            Text(caption.english).font(.system(size: max(14, fontSize * 0.6)))
                                .foregroundStyle(.white.opacity(0.7))
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                        .animation(.easeOut(duration: 0.15), value: caption.revision)
                } else {
                    Text(live.flags.needsMicrophone ? "等待讲话…" : "在主窗口开启课堂字幕")
                        .font(.system(size: fontSize)).foregroundStyle(.white.opacity(0.6))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }.foregroundStyle(.white)
        }.padding(.horizontal, 22).padding(.top, 32).padding(.bottom, 18)
            .background(Color.black.opacity(opacity))
    }
    private func primary(_ caption: LiveCaption) -> String {
        if !caption.chinese.isEmpty { return caption.chinese }
        if !live.flags.translate && !caption.english.isEmpty { return caption.english }
        return "正在理解…"
    }
}
