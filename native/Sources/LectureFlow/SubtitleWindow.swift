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
        let window = CaptionPanel(
            contentRect: NSRect(x: 280, y: 200, width: 560, height: 78),
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
        window.appearance = NSAppearance(named: .darkAqua)
        window.contentMinSize = NSSize(width: 320, height: 64)
        window.contentMaxSize = NSSize(width: 12_000, height: 900)
        window.setFrameAutosaveName("LectureFlowSubtitles.v5")

        let host = NSHostingView(
            rootView: FloatingSubtitlesRoot(workspace: workspace, live: workspace.live)
        )
        host.wantsLayer = true
        host.layer?.backgroundColor = NSColor.clear.cgColor
        window.contentView = host

        if !NSScreen.screens.contains(where: { $0.visibleFrame.intersects(window.frame) }) {
            window.center()
        }
        NotificationCenter.default.addObserver(
            forName: NSWindow.willCloseNotification, object: window, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.panel = nil }
        }
        panel = window
        window.orderFrontRegardless()
    }

    private weak var settingsOrigin: NSWindow?
    func focusSettings() {
        if NSApp.keyWindow !== panel { settingsOrigin = NSApp.keyWindow }
        panel?.makeKeyAndOrderFront(nil)
    }
    func leaveSettings() {
        settingsOrigin?.makeKeyAndOrderFront(nil)
        settingsOrigin = nil
    }

    func hide() {
        panel?.orderOut(nil)
    }

    func toggle(workspace: Workspace) {
        if isVisible { hide() } else { show(workspace: workspace) }
    }
}

/// Translucent edges preserve the user's opacity preference. Text and controls
/// have a local contrast floor so a white slide cannot wash them out.
private struct FloatingSubtitlesRoot: View {
    @ThemePalette private var palette
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    @AppStorage("subtitleSize") private var fontSize = 22.0
    @AppStorage("subtitleOpacity") private var opacity = 0.86
    @AppStorage("subtitleLanguage") private var language = "zh"
    @AppStorage("autoFloatingSubtitles") private var automatic = true
    @FocusState private var displayFocus: String?
    @State private var readingRows: [LiveCaption] = []
    @State private var follow = true
    @State private var hoveredCaptionID: String?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let trafficLightInset: CGFloat = 78
    private let titlebarRow: CGFloat = 28
    private let maxVisibleCaptions = 6

    private var panelFill: Color {
        palette.floating.opacity(contrast == .increased || reduceTransparency ? 1 : opacity)
    }

    var body: some View {
        GeometryReader { geo in
            let visibleCount = captionCapacity(for: geo.size.height)
            VStack(alignment: .leading, spacing: 0) {
                titlebar
                    .frame(height: titlebarRow)
                    .background(palette.floating.opacity(LFFloatingContrast.backingOpacity))
                captionStack(visibleCount: visibleCount)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 4)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
                    .background(palette.floating.opacity(LFFloatingContrast.backingOpacity),
                                in: RoundedRectangle(cornerRadius: 6))
                    .padding(.horizontal, 6)
                    .padding(.top, 2)
                    .padding(.bottom, 6)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
        }
        .frame(minWidth: 320, minHeight: minimumReadableHeight)
        .background(panelFill)
        .preferredColorScheme(.dark)
        // Draw under the system titlebar so chrome shares the traffic-light row.
        .ignoresSafeArea(.container, edges: .top)
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
            ViewThatFits(in: .horizontal) {
                statusCluster(includeText: true)
                statusCluster(includeText: false)
                Color.clear.frame(width: 0, height: 1)
            }
            .layoutPriority(0)

            Spacer(minLength: 4)

            ReviewCurrentButton(workspace: workspace, live: live, compact: true)
                .contextMenu {
                    Button("查看复习标记") { workspace.openReviewMarks() }
                }

            if workspace.session.hasSession {
                Button(workspace.session.primaryTitle) {
                    workspace.primarySessionAction()
                }
                .layoutPriority(1)
            }
            Button { workspace.subtitles.focusSettings(); workspace.subtitleSettingsPresented.toggle() } label: {
                Image(systemName: "slider.horizontal.3")
            }
            .help("字号与显示")
            .accessibilityLabel("字号与显示")
            .popover(isPresented: $workspace.subtitleSettingsPresented) {
                VStack(alignment: .leading, spacing: 12) {
                    Picker("显示", selection: $language) {
                        Text("仅中文").tag("zh")
                        Text("双语").tag("both")
                    }.pickerStyle(.segmented)
                    Toggle("开始实时字幕时自动显示", isOn: $automatic)
                    Text("字号 \(Int(fontSize))")
                    Slider(value: $fontSize, in: 16...56)
                        .focusable(true, interactions: .edit).focused($displayFocus, equals: "font")
                        .accessibilityLabel("字幕字号")
                        .adjustWithArrows($fontSize, range: 16...56, step: 1)
                    Text("背景透明度")
                    Slider(value: $opacity, in: 0.2...1)
                        .focusable(true, interactions: .edit).focused($displayFocus, equals: "opacity")
                        .accessibilityLabel("背景透明度")
                        .adjustWithArrows($opacity, range: 0.2...1, step: 0.05)
                    Text("拉高窗口可多看几句；默认约 1 句。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(16)
                .frame(width: 280)
                .onAppear { Task { @MainActor in await Task.yield(); displayFocus = "font" } }
                .onDisappear { workspace.subtitles.leaveSettings() }
            }
            .layoutPriority(1)

            Button {
                workspace.toggleMainWindow()
            } label: {
                Image(systemName: workspace.isMainWindowVisible ? "macwindow.fill" : "macwindow")
            }
            .help(workspace.isMainWindowVisible ? "隐藏主窗口" : "显示主窗口")
            .accessibilityLabel(workspace.isMainWindowVisible ? "隐藏主窗口" : "显示主窗口")
            .layoutPriority(1)

            Button { workspace.hideSubtitles() } label: {
                Image(systemName: "eye.slash")
            }
            .help("隐藏悬浮字幕")
            .accessibilityLabel("隐藏悬浮字幕")
            .layoutPriority(1)
        }
        .font(.system(size: 10, weight: .medium))
        .buttonStyle(.plain)
        .foregroundStyle(.white.opacity(0.9))
        .padding(.leading, trafficLightInset)
        .padding(.trailing, 8)
        .frame(maxWidth: .infinity, minHeight: titlebarRow, maxHeight: titlebarRow)
        .contentShape(Rectangle())
    }

    @ViewBuilder
    private func statusCluster(includeText: Bool) -> some View {
        HStack(spacing: 5) {
            Circle()
                .fill(statusColor)
                .frame(width: 5, height: 5)
            if includeText {
                Text(compactStatus)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
                    .help(compactStatus)
            }
        }
        .font(.system(size: 11, weight: .medium))
        .padding(.horizontal, includeText ? 6 : 2).padding(.vertical, 3)
        .background(.white.opacity(includeText ? 0.12 : 0), in: Capsule())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(compactStatus)
    }

    @ViewBuilder
    private func captionStack(visibleCount: Int) -> some View {
        let captions = follow ? Array(live.displayCaptions.suffix(visibleCount)) : readingRows.map { row in
            live.displayCaptions.last(where: { $0.id == row.id && $0.session_id == row.session_id }) ?? row
        }
        ScrollViewReader { proxy in
            VStack(alignment: .leading, spacing: 0) {
                if !follow {
                    HStack {
                        Spacer()
                        Button("最新") {
                            readingRows = []
                            follow = true
                            proxy.scrollTo(FloatingCaptionAnchor.tail, anchor: .bottom)
                        }
                            .font(.system(size: 10, weight: .medium))
                            .buttonStyle(.plain)
                            .foregroundStyle(.white.opacity(0.7))
                    }
                    .padding(.bottom, 2)
                }
                FloatingCaptionContent(captions: captions, waiting: live.flags.needsASR,
                                       fontSize: fontSize, spacing: unitSpacing) { caption in
                    captionBlock(caption)
                }
                .background(ScrollActivityObserver {
                    if follow && !captions.isEmpty {
                        readingRows = captions
                        follow = false
                    }
                })
                .onChange(of: live.displayCaptions.last?.session_id) { _, _ in
                    readingRows = []
                    follow = true
                }
                .onChange(of: captions.map { "\($0.session_id ?? ""):\($0.id):\($0.revision)" }) { _, _ in
                    guard follow, !captions.isEmpty else { return }
                    // Streaming text and late translations update immediately;
                    // animate user actions only, never each revision's geometry.
                    proxy.scrollTo(FloatingCaptionAnchor.tail, anchor: .bottom)
                }
                .onChange(of: visibleCount) { _, _ in
                    guard follow else { return }
                    proxy.scrollTo(FloatingCaptionAnchor.tail, anchor: .bottom)
                }
            }
            .foregroundStyle(.white)
        }
    }

    @ViewBuilder
    private func captionBlock(_ caption: LiveCaption) -> some View {
        HStack(alignment: .top, spacing: 8) {
        VStack(alignment: .leading, spacing: 2) {
            if language != "en", !caption.chinese.isEmpty {
                Text(caption.chinese)
                    .font(.system(size: fontSize, weight: .medium))
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if language == "both", !caption.english.isEmpty {
                Text(caption.english)
                    .font(.system(size: max(12, fontSize * 0.58)))
                    .foregroundStyle(.white.opacity(LFFloatingContrast.englishOpacity))
                    .lineSpacing(1)
                    .fixedSize(horizontal: false, vertical: true)
            } else if language != "both", caption.chinese.isEmpty, !caption.english.isEmpty {
                Text(caption.english)
                    .font(.system(size: fontSize, weight: .medium))
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .textSelection(.enabled)
        .opacity(caption.id == live.displayCaptions.last?.id ? 1 : LFFloatingContrast.olderTextOpacity)
        ReviewCaptionButton(workspace: workspace, caption: caption, compact: true,
                            revealed: hoveredCaptionID == caption.id)
        }
        .contentShape(Rectangle())
        .background(ReviewHoverRegion { inside in
            if inside { hoveredCaptionID = caption.id }
            else if hoveredCaptionID == caption.id { hoveredCaptionID = nil }
        })
    }

    private func captionCapacity(for height: CGFloat) -> Int {
        let usable = max(0, height - titlebarRow - 8)
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

    private var minimumReadableHeight: CGFloat {
        // A scroll view shorter than one glyph cannot reveal that glyph even
        // by scrolling. Keep at least one line of each selected language.
        ceil(titlebarRow + estimatedUnitHeight + 14)
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


private extension View {
    func adjustWithArrows(_ value: Binding<Double>, range: ClosedRange<Double>, step: Double) -> some View {
        onKeyPress(keys: [.leftArrow, .rightArrow], phases: [.down, .repeat]) { event in
            guard event.modifiers.isDisjoint(with: [.command, .control, .option, .shift]) else {
                return .ignored
            }
            let next = value.wrappedValue + (event.key == .rightArrow ? step : -step)
            value.wrappedValue = min(range.upperBound, max(range.lowerBound, next))
            return .handled
        }
    }
}
