import AppKit
import LectureFlowCore
import SwiftUI

private final class CaptionPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class SubtitleWindow {
    private var panel: NSPanel?
    func show(workspace: Workspace) {
        if let panel { panel.orderFrontRegardless(); return }
        let window = CaptionPanel(contentRect: NSRect(x: 260, y: 160, width: 680, height: 235),
            styleMask: [.titled, .closable, .resizable, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = "LectureFlow 字幕"
        window.level = .statusBar
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        window.hidesOnDeactivate = false
        window.becomesKeyOnlyIfNeeded = true
        window.isMovableByWindowBackground = true
        window.isReleasedWhenClosed = false
        window.isOpaque = false
        window.backgroundColor = .clear
        window.titlebarAppearsTransparent = true
        window.contentMinSize = NSSize(width: 460, height: 150)
        window.setFrameAutosaveName("LectureFlowSubtitles")
        if !NSScreen.screens.contains(where: { $0.visibleFrame.intersects(window.frame) }) { window.center() }
        window.contentView = NSHostingView(rootView: FloatingSubtitles(workspace: workspace, live: workspace.live))
        panel = window
        window.orderFrontRegardless()
    }
}

private struct FloatingSubtitles: View {
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    @AppStorage("subtitleSize") private var fontSize = 28.0
    @AppStorage("subtitleOpacity") private var opacity = 0.86
    @AppStorage("subtitleEnglish") private var showEnglish = true
    @AppStorage("autoFloatingSubtitles") private var automatic = true
    @State private var settings = false
    @State private var resumeFlags = CaptureFlags(recording: false, english: true, translate: true)

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Circle().fill(live.flags.needsMicrophone ? Color.mint : Color.gray).frame(width: 6, height: 6)
                Picker("课堂", selection: Binding(get: { workspace.selectedLecture }, set: { workspace.selectLecture($0) })) {
                    Text("选择课堂").tag(String?.none)
                    ForEach(workspace.overview.courses) { course in
                        ForEach(course.lectures) { lecture in
                            Text("\(course.name) · \(lecture.title)").tag(Optional(lecture.id))
                        }
                    }
                }.labelsHidden().frame(maxWidth: 250).disabled(live.flags.needsMicrophone)
                Spacer(minLength: 0)
                Button(live.flags.needsMicrophone ? "暂停" : "开始字幕", systemImage: live.flags.needsMicrophone ? "pause.fill" : "play.fill") {
                    if live.flags.needsMicrophone { resumeFlags = live.flags; workspace.changeCapture(.paused) }
                    else { workspace.changeCapture(resumeFlags) }
                }.disabled(workspace.lecture == nil || workspace.busy)
                Button { workspace.showMainWindow() } label: {
                    Image(systemName: "rectangle.inset.filled")
                }.help("返回主窗口")
                Button { settings.toggle() } label: { Image(systemName: "slider.horizontal.3") }
                    .help("字幕样式与采集设置")
                    .popover(isPresented: $settings) {
                        VStack(alignment: .leading, spacing: 14) {
                            Toggle("显示英文", isOn: $showEnglish)
                            Toggle("开启实时模式时自动悬浮", isOn: $automatic)
                            Text("字号 \(Int(fontSize))"); Slider(value: $fontSize, in: 18...64)
                            Text("背景透明度"); Slider(value: $opacity, in: 0.2...1)
                            Divider()
                            Toggle("保存录音", isOn: captureBinding(\.recording))
                            Toggle("保存英文转写", isOn: captureBinding(\.english))
                            Toggle("中文翻译", isOn: captureBinding(\.translate))
                            Text("可关闭主窗口继续听课；⌘⌥L 随时显示字幕。")
                                .font(.caption).foregroundStyle(.secondary)
                        }.toggleStyle(.checkbox).padding(20).frame(width: 290)
                    }
            }.font(.caption).buttonStyle(.plain).foregroundStyle(.white.opacity(0.8))
            ScrollView {
                if let caption = live.captions.last {
                    VStack(alignment: .leading, spacing: 9) {
                        Text(primary(caption)).font(.system(size: fontSize, weight: .medium)).lineSpacing(4)
                            .contentTransition(.interpolate)
                        if showEnglish && !caption.chinese.isEmpty && !caption.english.isEmpty {
                            Text(caption.english).font(.system(size: max(14, fontSize * 0.62)))
                                .foregroundStyle(.white.opacity(0.68))
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                        .animation(.easeOut(duration: 0.15), value: caption.revision)
                } else {
                    VStack(alignment: .leading, spacing: 9) {
                        Text(live.flags.needsASR ? "等待讲话…" : "专心听课，字幕留在这里")
                            .font(.system(size: fontSize, weight: .medium))
                        Text(live.flags.needsASR ? live.message : "选择课堂后点“开始字幕”，即可切到课件或视频。")
                            .font(.callout).foregroundStyle(.white.opacity(0.6))
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            }.foregroundStyle(.white)
            HStack {
                Text(live.flags.recording ? "● 录音保存中" : "未保存原始音频")
                Spacer()
                Text(live.flags.needsMicrophone ? "本机处理" : "已暂停")
            }.font(.system(size: 10)).foregroundStyle(.white.opacity(0.45))
        }.padding(.horizontal, 20).padding(.top, 30).padding(.bottom, 12)
            .background(Color(red: 0.06, green: 0.08, blue: 0.12).opacity(opacity))
            .preferredColorScheme(.dark)
            .onChange(of: live.error) { _, value in if let value { workspace.error = value } }
            .alert("操作未完成", isPresented: Binding(get: { workspace.error != nil }, set: { if !$0 { workspace.error = nil } })) {
                Button("知道了") { workspace.error = nil; live.error = nil }
            } message: { Text(workspace.error ?? "") }
    }
    private func captureBinding(_ key: WritableKeyPath<CaptureFlags, Bool>) -> Binding<Bool> {
        Binding(get: { live.flags[keyPath: key] }, set: { value in
            var flags = live.flags; flags[keyPath: key] = value; workspace.changeCapture(flags)
        })
    }
    private func primary(_ caption: LiveCaption) -> String {
        if !caption.chinese.isEmpty { return caption.chinese }
        return caption.english.isEmpty ? "正在理解…" : caption.english
    }
}
