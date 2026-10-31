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

    var isVisible: Bool { panel?.isVisible == true }

    func show(workspace: Workspace) {
        if let panel {
            panel.orderFrontRegardless()
            return
        }
        // One titlebar row with traffic lights + controls; grow taller for more captions.
        let window = CaptionPanel(
            contentRect: NSRect(x: 280, y: 200, width: 560, height: 92),
            styleMask: [.titled, .closable, .resizable, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "LectureFlow 字幕"
        window.titleVisibility = .hidden
        window.level = .statusBar
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        window.hidesOnDeactivate = false
        window.becomesKeyOnlyIfNeeded = true
        window.isMovableByWindowBackground = true
        window.isReleasedWhenClosed = false
        window.isOpaque = false
        window.backgroundColor = .clear
        window.titlebarAppearsTransparent = true
        window.toolbarStyle = .unifiedCompact
        window.contentMinSize = NSSize(width: 360, height: 72)
        window.contentMaxSize = NSSize(width: 12_000, height: 900)
        window.setFrameAutosaveName("LectureFlowSubtitles.v3")
        if !NSScreen.screens.contains(where: { $0.visibleFrame.intersects(window.frame) }) {
            window.center()
        }
        window.contentView = NSHostingView(
            rootView: FloatingSubtitles(workspace: workspace, live: workspace.live)
        )
        NotificationCenter.default.addObserver(
            forName: NSWindow.willCloseNotification, object: window, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.panel = nil }
        }
        panel = window
        window.orderFrontRegardless()
    }

    func hide() {
        panel?.orderOut(nil)
    }

    func toggle(workspace: Workspace) {
        if isVisible { hide() } else { show(workspace: workspace) }
    }
}

private struct FloatingSubtitles: View {
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    @AppStorage("subtitleSize") private var fontSize = 22.0
    @AppStorage("subtitleOpacity") private var opacity = 0.86
    @AppStorage("subtitleLanguage") private var language = "zh" // zh | both
    @AppStorage("autoFloatingSubtitles") private var automatic = true
    @State private var settings = false
    @State private var follow = true

    /// Same row as macOS traffic lights (leading inset clears the buttons).
    private let titlebarRow: CGFloat = 28
    private let trafficLightInset: CGFloat = 72
    private let maxVisibleCaptions = 6

    var body: some View {
        GeometryReader { geo in
            let visibleCount = captionCapacity(for: geo.size.height)
            VStack(alignment: .leading, spacing: 0) {
                titlebar
                    .frame(height: titlebarRow)
                captionStack(visibleCount: visibleCount)
                    .padding(.horizontal, 12)
                    .padding(.top, 4)
                    .padding(.bottom, 6)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
        }
        .background(Color(red: 0.06, green: 0.08, blue: 0.12).opacity(opacity))
        .preferredColorScheme(.dark)
        .onChange(of: live.error) { _, value in if let value { workspace.error = value } }
        .alert(
            "操作未完成",
            isPresented: Binding(
                get: { workspace.error != nil },
                set: { if !$0 { workspace.error = nil } }
            )
        ) {
            Button("知道了") { workspace.error = nil; live.error = nil }
        } message: {
            Text(workspace.error ?? "")
        }
    }

    private var titlebar: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(statusColor)
                .frame(width: 5, height: 5)
            Text(compactStatus)
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(1)
            Spacer(minLength: 6)
            if workspace.session.hasSession {
                Button(workspace.session.primaryTitle) {
                    workspace.primarySessionAction()
                }
            }
            if !follow {
                Button("最新") { follow = true }
            }
            Button { settings.toggle() } label: {
                Image(systemName: "slider.horizontal.3")
            }
            .help("字号与显示")
            .popover(isPresented: $settings) {
                VStack(alignment: .leading, spacing: 12) {
                    Picker("显示", selection: $language) {
                        Text("仅中文").tag("zh")
                        Text("双语").tag("both")
                    }.pickerStyle(.segmented)
                    Toggle("开始实时字幕时自动显示", isOn: $automatic)
                    Text("字号 \(Int(fontSize))")
                    Slider(value: $fontSize, in: 16...56)
                    Text("背景透明度")
                    Slider(value: $opacity, in: 0.2...1)
                    Text("拉高窗口可多看几句；默认约 1 句。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(16)
                .frame(width: 280)
            }
            Button {
                workspace.showMainWindow()
            } label: {
                Image(systemName: "macwindow")
            }
            .help("返回主窗口")
            .accessibilityLabel("主窗口")
            Button {
                workspace.hideSubtitles()
            } label: {
                Image(systemName: "eye.slash")
            }
            .help("隐藏悬浮字幕")
        }
        .font(.system(size: 10, weight: .medium))
        .buttonStyle(.plain)
        .foregroundStyle(.white.opacity(0.88))
        .padding(.leading, trafficLightInset)
        .padding(.trailing, 10)
        .contentShape(Rectangle())
    }

    @ViewBuilder
    private func captionStack(visibleCount: Int) -> some View {
        let captions = Array(live.captions.suffix(visibleCount))
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: unitSpacing) {
                    if captions.isEmpty {
                        Text(live.flags.needsASR ? "等待讲话…" : "字幕将显示在这里")
                            .font(.system(size: fontSize, weight: .medium))
                            .foregroundStyle(.white.opacity(0.75))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .id("latest")
                    } else {
                        ForEach(captions) { caption in
                            captionBlock(caption)
                                .id(caption.id)
                        }
                        Color.clear.frame(height: 1).id("latest")
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .foregroundStyle(.white)
            .scrollIndicators(.hidden)
            .simultaneousGesture(DragGesture().onChanged { _ in follow = false })
            .onChange(of: live.captions.last?.revision) { _, _ in
                guard follow else { return }
                withAnimation(.easeOut(duration: 0.12)) {
                    proxy.scrollTo("latest", anchor: .bottom)
                }
            }
            .onChange(of: visibleCount) { _, _ in
                guard follow else { return }
                proxy.scrollTo("latest", anchor: .bottom)
            }
        }
    }

    @ViewBuilder
    private func captionBlock(_ caption: LiveCaption) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            if language != "en", !caption.chinese.isEmpty {
                Text(caption.chinese)
                    .font(.system(size: fontSize, weight: .medium))
                    .lineSpacing(2)
                    .lineLimit(language == "both" ? 3 : 4)
            }
            if language == "both", !caption.english.isEmpty {
                Text(caption.english)
                    .font(.system(size: max(12, fontSize * 0.58)))
                    .foregroundStyle(.white.opacity(0.62))
                    .lineSpacing(1)
                    .lineLimit(2)
            } else if language != "both", caption.chinese.isEmpty, !caption.english.isEmpty {
                Text(caption.english)
                    .font(.system(size: fontSize, weight: .medium))
                    .lineSpacing(2)
                    .lineLimit(4)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .textSelection(.enabled)
        .opacity(caption.id == live.captions.last?.id ? 1 : 0.72)
        .animation(.easeOut(duration: 0.12), value: caption.revision)
    }

    private func captionCapacity(for height: CGFloat) -> Int {
        let usable = max(0, height - titlebarRow - 10)
        let count = Int(floor(usable / max(estimatedUnitHeight, 1)))
        return min(maxVisibleCaptions, max(1, count))
    }

    private var estimatedUnitHeight: CGFloat {
        let primary = fontSize * 1.28
        if language == "both" {
            return primary + max(12, fontSize * 0.58) * 1.15 + unitSpacing
        }
        return primary + unitSpacing
    }

    private var unitSpacing: CGFloat { 6 }

    private var statusColor: Color {
        if workspace.session.state == .paused { return .orange }
        if live.flags.needsMicrophone { return .mint }
        return .gray
    }

    private var compactStatus: String {
        if workspace.session.state == .paused { return "已暂停" }
        if live.flags.recording { return "录音中 · 保存中" }
        if live.flags.needsASR { return "仅字幕" }
        if workspace.session.hasSession {
            return workspace.session.desired.recording ? "已暂停" : "不保存音频"
        }
        return "已停止"
    }
}
