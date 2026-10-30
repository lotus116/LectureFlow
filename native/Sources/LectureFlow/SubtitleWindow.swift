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
    @AppStorage("subtitleLanguage") private var language = "zh" // zh | both
    @AppStorage("autoFloatingSubtitles") private var automatic = true
    @State private var settings = false
    @State private var follow = true

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Circle().fill(statusColor).frame(width: 6, height: 6)
                Text(statusLabel).font(.caption.weight(.medium))
                Spacer(minLength: 0)
                if workspace.session.hasSession {
                    Button(workspace.session.primaryTitle) {
                        workspace.primarySessionAction()
                    }
                }
                Button("返回主窗口") { workspace.showMainWindow() }
                Button { settings.toggle() } label: { Image(systemName: "slider.horizontal.3") }
                    .help("字号与显示")
                    .popover(isPresented: $settings) {
                        VStack(alignment: .leading, spacing: 14) {
                            Picker("显示", selection: $language) {
                                Text("仅中文").tag("zh")
                                Text("双语").tag("both")
                            }.pickerStyle(.segmented)
                            Toggle("开始实时字幕时自动显示", isOn: $automatic)
                            Text("字号 \(Int(fontSize))"); Slider(value: $fontSize, in: 18...64)
                            Text("背景透明度"); Slider(value: $opacity, in: 0.2...1)
                            Text("关闭本窗口只隐藏；结束 Lecture 请在主窗口操作。")
                                .font(.caption).foregroundStyle(.secondary)
                        }.padding(20).frame(width: 320)
                    }
                Button { panelHide() } label: { Image(systemName: "eye.slash") }
                    .help("隐藏悬浮字幕")
            }.font(.caption).buttonStyle(.plain).foregroundStyle(.white.opacity(0.85))

            ScrollViewReader { proxy in
                ScrollView {
                    if let caption = live.captions.last {
                        VStack(alignment: .leading, spacing: 9) {
                            if language != "en", !caption.chinese.isEmpty {
                                Text(caption.chinese).font(.system(size: fontSize, weight: .medium)).lineSpacing(4)
                            }
                            if language == "both", !caption.english.isEmpty {
                                Text(caption.english).font(.system(size: max(14, fontSize * 0.62)))
                                    .foregroundStyle(.white.opacity(0.68))
                            } else if language != "both", caption.chinese.isEmpty, !caption.english.isEmpty {
                                Text(caption.english).font(.system(size: fontSize, weight: .medium))
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .id("latest")
                        .animation(.easeOut(duration: 0.15), value: caption.revision)
                    } else {
                        Text(live.flags.needsASR ? "等待讲话…" : "字幕将显示在这里")
                            .font(.system(size: fontSize, weight: .medium))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .id("latest")
                    }
                }
                .foregroundStyle(.white)
                .simultaneousGesture(DragGesture().onChanged { _ in follow = false })
                .onChange(of: live.captions.last?.revision) { _, _ in
                    guard follow else { return }
                    withAnimation { proxy.scrollTo("latest", anchor: .bottom) }
                }
            }

            HStack {
                Text(footerLabel)
                Spacer()
                if !follow {
                    Button("返回最新") { follow = true }
                }
            }.font(.system(size: 10)).foregroundStyle(.white.opacity(0.45))
        }.padding(.horizontal, 20).padding(.top, 30).padding(.bottom, 12)
            .background(Color(red: 0.06, green: 0.08, blue: 0.12).opacity(opacity))
            .preferredColorScheme(.dark)
            .onChange(of: live.error) { _, value in if let value { workspace.error = value } }
            .alert("操作未完成", isPresented: Binding(get: { workspace.error != nil }, set: { if !$0 { workspace.error = nil } })) {
                Button("知道了") { workspace.error = nil; live.error = nil }
            } message: { Text(workspace.error ?? "") }
    }

    private var statusColor: Color {
        if workspace.session.state == .paused { return .orange }
        if live.flags.needsMicrophone { return .mint }
        return .gray
    }
    private var statusLabel: String {
        if workspace.session.state == .paused { return "已暂停" }
        if live.flags.recording { return "录音中" }
        if live.flags.needsASR { return "不保存音频" }
        return "已隐藏采集"
    }
    private var footerLabel: String {
        if live.flags.recording { return "● 录音保存中" }
        if workspace.session.hasSession {
            return workspace.session.desired.recording ? "录音已暂停，文件保留" : "不保存原始音频"
        }
        return "仅字幕模式不保存录音"
    }
    private func panelHide() {
        for window in NSApp.windows where window.title == "LectureFlow 字幕" {
            window.orderOut(nil)
        }
    }
}
