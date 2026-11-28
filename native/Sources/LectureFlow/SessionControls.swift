import Combine
import LectureFlowCore
import SwiftUI

struct SessionControlBar: View {
    @ThemePalette private var palette
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    let showingAudio: Bool
    var readingMode = false
    var hasLiveCaptionControls = false
    @State private var options = false
    @Environment(\.lfCompactChrome) private var compact

    private var active: Bool {
        workspace.session.state == .running || workspace.session.state == .preparing
    }

    private var paused: Bool { workspace.session.state == .paused }
    private var inlineNotice: String? { workspace.notice ?? secondaryDetail }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if workspace.session.state == .ended {
                endedSummary
            } else {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 3) {
                        statusCluster
                        TimelineView(.periodic(from: .now, by: 0.5)) { _ in
                            Text(clock(workspace.session.duration(at: ProcessInfo.processInfo.systemUptime)))
                                .font(.system(size: 20, weight: .semibold).monospacedDigit())
                                .accessibilityLabel("采集计时")
                        }
                    }
                    RecordingWaveformStrip(level: live.level, active: live.isCapturing || active, paused: paused)
                        .frame(width: compact ? 48 : 96, height: 28).accessibilityHidden(true)
                    Spacer(minLength: 8)
                    transportPrimary
                    if workspace.session.hasSession { transportStop }
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    Text(audioHint).font(.caption).foregroundStyle(palette.secondaryText)
                    Spacer(minLength: 8)
                    reviewTools
                    metaTools
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text(audioHint).font(.caption).foregroundStyle(palette.secondaryText)
                    HStack { reviewTools; Spacer(minLength: 8); metaTools }
                }
            }

            if let notice = inlineNotice {
                LFStatusBanner(notice, tone: noticeTone)
                    .accessibilityIdentifier("session-notice")
            }

            if workspace.session.state == .running && workspace.session.desired.needsASR
                && !live.flags.needsASR && !workspace.captionsWaiting {
                Button("重新连接字幕") { workspace.reconnectCaptions() }
                    .font(.system(size: LFChrome.captionSize))
            }

        }
        .padding(.horizontal, 16).padding(.vertical, 6)
        .background(active || paused ? palette.recorder : palette.panel)
        .overlay(alignment: .bottom) { Rectangle().fill(palette.hairline).frame(height: 1) }
    }

    private var reviewTools: some View {
        HStack(spacing: 10) {
                if !workspace.session.desired.needsASR && !hasLiveCaptionControls {
                    ReviewCurrentButton(workspace: workspace, live: live)
                }
                Button("复习标记\(workspace.reviews.activeCount == 0 ? "" : " · \(workspace.reviews.activeCount)")") {
                    workspace.openReviewMarks()
                }
                .disabled(workspace.lecture == nil || workspace.isExiting)
                .accessibilityIdentifier("review-marks-open")
                if let feedback = workspace.reviews.feedback {
                    Text(feedback).font(.caption).foregroundStyle(palette.secondaryText)
                }
                if workspace.reviews.hasUnsavedChanges && !workspace.reviews.saving {
                    Button("重试保存标记") { workspace.reviews.retryFailed() }
                }
            }.controlSize(.small)
    }

    private var transportPrimary: some View {
        Button {
            workspace.primarySessionAction()
        } label: {
            Label(workspace.session.primaryTitle, systemImage: active ? "pause.fill" : (paused ? "play.fill" : "record.circle"))
                .font(.system(size: 13, weight: .semibold))
                .padding(.horizontal, 4).padding(.vertical, 6)
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderedProminent)
        .tint(palette.accent)

        .disabled(!active && !paused && (workspace.busy || workspace.lecture == nil))
        .help(workspace.session.primaryTitle)
        .accessibilityLabel(workspace.session.primaryTitle)
        .accessibilityIdentifier("session-primary")
    }

    private var transportStop: some View {
        Button {
            workspace.endSession()
        } label: {
            Label("结束 Lecture", systemImage: "stop.fill")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(palette.dangerInk)
                .padding(.horizontal, 4).padding(.vertical, 6)
                .contentShape(Rectangle())
        }
        .buttonStyle(.bordered)

        .help("结束 Lecture；不会删除已有内容")
        .accessibilityLabel("结束 Lecture")
        .accessibilityIdentifier("session-end")
    }

    private var statusCluster: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(statusColor)
                .frame(width: 7, height: 7)
            Text(status)
                .font(.system(size: LFChrome.captionSize, weight: .semibold))
                .foregroundStyle(palette.bodyText)
                .lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }

    private var flexibleDetail: some View {
        HStack(spacing: 6) {
            if workspace.session.state != .ended {
                Text("·").foregroundStyle(.tertiary).accessibilityHidden(true)
            }
            Text(audioHint)
                .font(.system(size: LFChrome.captionSize))
                .foregroundStyle(palette.secondaryText)
                .lineLimit(1)
                .truncationMode(.tail)
        }
        .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
    }

    private var metaTools: some View {
        HStack(spacing: LFChrome.space8) {
            Button {
                options.toggle()
            } label: {
                Image(systemName: "slider.horizontal.3")
            }
            .help("记录方式")
            .accessibilityLabel("记录方式")
            .popover(isPresented: $options) {
                CaptureOptionsView(workspace: workspace)
            }

            ModelSelector(workspace: workspace, live: live)
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    private var endedSummary: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: LFChrome.space12) {
                savedSummary
                Spacer(minLength: 8)
                endedActions
            }
            VStack(alignment: .leading, spacing: LFChrome.space8) {
                savedSummary
                endedActions.frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
    }

    private var savedSummary: some View {
        VStack(alignment: .leading, spacing: 4) {
            endedStatusLabel
            Text("最近采集 " + clock(workspace.session.duration(at: ProcessInfo.processInfo.systemUptime)))
                .font(.system(size: LFChrome.captionSize).monospacedDigit())
                .foregroundStyle(palette.secondaryText)
        }
    }

    private var endedStatusLabel: some View {
        Label(
            live.pendingSaves > 0 ? "正在写入资料库…" : "采集已结束 · 内容已保留",
            systemImage: live.pendingSaves > 0 ? "arrow.down.circle" : "checkmark.circle.fill"
        )
        .font(.system(size: LFChrome.captionSize))
        .foregroundStyle(live.pendingSaves > 0 ? palette.accentInk : palette.saved)
        .lineLimit(1)
        .fixedSize(horizontal: true, vertical: true)
    }

    private var endedActions: some View {
        HStack(spacing: LFChrome.space8) {
            Button(workspace.session.desired.recording ? "继续录制" : "继续字幕") { workspace.primarySessionAction() }
                .disabled(workspace.busy || workspace.lecture == nil)
            if !showingAudio {
                Button("查看录音") { workspace.requestedTab = "audio" }
                Button("生成课后转写") {
                    let lectureID = workspace.selectedLecture
                    Task {
                        await workspace.enqueue()
                        if workspace.selectedLecture == lectureID { workspace.requestedTab = "offline" }
                    }
                }
                    .buttonStyle(.borderedProminent)
                    .tint(palette.accent)
                    .disabled(workspace.lecture?.segments.isEmpty != false || workspace.busy || live.pendingSaves > 0)
            }
        }
        .buttonStyle(.bordered).controlSize(.regular)
        .fixedSize(horizontal: true, vertical: false)
    }

    private var statusColor: Color {
        if active { return palette.recordSignal }
        if paused { return .orange }
        return workspace.session.state == .ended ? palette.saved : palette.accentInk
    }

    private var status: String {
        switch workspace.session.state {
        case .idle: return "准备开始"
        case .preparing: return "正在打开麦克风…"
        case .running: return live.flags.recording ? "正在录音" : "字幕进行中"
        case .paused: return "已暂停"
        case .ended: return "已结束"
        }
    }

    private var audioHint: String {
        if !workspace.session.desired.recording { return "不保存音频 · \(workspace.session.modeTitle)" }
        return workspace.session.modeTitle
    }

    private var noticeTone: LFStatusTone {
        if live.recoveryExhausted { return .failure }
        if workspace.captionsWaiting { return .information }
        // Opaque backend text remains unchanged; classify only its visual emphasis.
        if let notice = inlineNotice, notice.contains("中断") || notice.contains("恢复") { return .attention }
        return live.pendingSaves > 0 ? .working : .information
    }

    private var secondaryDetail: String? {
        if live.pendingSaves > 0 { return "录音已封存，正在加入本 Lecture。" }
        if active {
            return workspace.captionsWaiting ? "录音已开始，字幕等待模型就绪。" : (live.message.isEmpty ? nil : live.message)
        }
        return nil
    }

    private func clock(_ seconds: Double) -> String {
        let value = max(0, Int(seconds))
        return String(format: "%d:%02d:%02d", value / 3600, value / 60 % 60, value % 60)
    }
}

// MARK: - Waveform

/// Scrolling mic waveform with an orange live playhead (voice-memo style).
private struct RecordingWaveformStrip: View {
    @ThemePalette private var palette
    var level: Float
    var active: Bool
    var paused: Bool

    @State private var samples: [CGFloat] = Array(repeating: 0.12, count: 48)
    @State private var latest: CGFloat = 0.12
    private let crawl = Timer.publish(every: 0.09, on: .main, in: .common).autoconnect()
    private let barWidth: CGFloat = 2.2
    private let barSpacing: CGFloat = 1.6

    var body: some View {
        GeometryReader { geo in
            let count = max(16, min(128, Int((geo.size.width + barSpacing) / (barWidth + barSpacing))))
            let playheadX = geo.size.width * 0.78
            let heights = resampled(count: count)

            ZStack(alignment: .leading) {
                HStack(alignment: .center, spacing: barSpacing) {
                    ForEach(0..<count, id: \.self) { index in
                        let x = CGFloat(index) * (barWidth + barSpacing) + barWidth / 2
                        let past = x <= playheadX
                        Capsule(style: .continuous)
                            .fill(barColor(past: past))
                            .frame(width: barWidth, height: barHeight(heights[index], in: geo.size.height))
                            .opacity(past ? 1 : 0.45)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)

                Capsule()
                    .fill(palette.recordSignal)
                    .frame(width: 2, height: geo.size.height * 0.92)
                    .offset(x: playheadX - 1)
                    .accessibilityHidden(true)
            }
            .onChange(of: count) { _, newCount in
                resizeBuffer(to: newCount)
            }
            .onAppear { latest = CGFloat(level); resizeBuffer(to: count) }
        }
        .clipped()
        .onChange(of: level) { _, newValue in
            latest = CGFloat(newValue)
        }
        // Crawl while capturing; freeze when paused; idle stays still.
        .onReceive(crawl) { _ in
            guard active, !paused else { return }
            pushSample(max(0.08, latest))
        }
        .opacity(active || paused ? 1 : 0.55)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("录音波形")
        .accessibilityValue(active ? "采集中" : (paused ? "已暂停" : "未采集"))
        .help("麦克风输入波形")
    }

    private func barHeight(_ sample: CGFloat, in height: CGFloat) -> CGFloat {
        let minH = max(4, height * 0.18)
        let maxH = height * 0.92
        let shaped = min(1, max(0.06, sample))
        return minH + (maxH - minH) * shaped
    }

    private func barColor(past: Bool) -> Color {
        if paused {
            return past ? Color.primary.opacity(0.55) : Color.primary.opacity(0.18)
        }
        if !active {
            return Color.primary.opacity(past ? 0.28 : 0.12)
        }
        return past ? Color.primary.opacity(0.88) : Color.primary.opacity(0.2)
    }

    private func pushSample(_ value: CGFloat) {
        guard !samples.isEmpty else { return }
        var next = samples
        next.removeFirst()
        // Mild shaping so quiet speech still reads as activity.
        let shaped = min(1, max(0.06, value * 1.35))
        next.append(paused ? (next.last ?? 0.12) : shaped)
        samples = next
    }

    private func resizeBuffer(to count: Int) {
        guard count > 0 else { return }
        if samples.count == count { return }
        if samples.count < count {
            samples = Array(repeating: 0.12, count: count - samples.count) + samples
        } else {
            samples = Array(samples.suffix(count))
        }
    }

    private func resampled(count: Int) -> [CGFloat] {
        if samples.count == count { return samples }
        if samples.isEmpty { return Array(repeating: 0.12, count: count) }
        if samples.count > count { return Array(samples.suffix(count)) }
        return Array(repeating: 0.12, count: count - samples.count) + samples
    }
}

struct CaptureOptionsView: View {
    @ThemePalette private var palette
    @ObservedObject var workspace: Workspace
    @State private var cancelling = false
    var body: some View {
        VStack(alignment: .leading, spacing: LFChrome.space12) {
            Text("记录方式").font(.headline)
            ForEach(CapturePreset.allCases, id: \.self) { preset in
                Button { workspace.configureSession(preset.flags) } label: {
                    HStack {
                        Image(systemName: workspace.session.desired == preset.flags ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(palette.accentInk)
                        Text(preset.title)
                        Spacer()
                    }.padding(.vertical, 3)
                }
                .buttonStyle(.plain)
                .disabled(!workspace.session.canConfigure)
            }
            if !workspace.session.canConfigure {
                Text("先暂停，再调整记录方式。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Divider()
            DisclosureGroup("更多选项") {
                VStack(alignment: .leading, spacing: 10) {
                    Toggle("保存原始录音", isOn: binding(\.recording))
                    Toggle("保存原文转写", isOn: binding(\.english))
                    Toggle("翻译为中文", isOn: binding(\.translate))
                }
                .toggleStyle(.checkbox)
                .padding(.top, 8)
                .disabled(!workspace.session.canConfigure)
                if workspace.live.flags.recording {
                    Button("取消当前录音段", role: .destructive) { cancelling = true }
                        .padding(.top, 10)
                }
            }
        }
        .padding(LFChrome.space16)
        .frame(width: 300)
        .confirmationDialog(
            "取消当前录音段？录音会移入废纸篓，字幕按原设置继续。",
            isPresented: $cancelling
        ) {
            Button("取消当前录音段", role: .destructive) { workspace.live.cancelCurrentRecording() }
        }
    }

    private func binding(_ key: WritableKeyPath<CaptureFlags, Bool>) -> Binding<Bool> {
        Binding(get: { workspace.session.desired[keyPath: key] }, set: { value in
            var flags = workspace.session.desired
            flags[keyPath: key] = value
            workspace.configureSession(flags)
        })
    }
}

struct ModelSelector: View {
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    @State private var showing = false
    private var modelTitle: String { workspace.model == "asr-large" ? "高精度" : "标准识别" }
    var body: some View {
        Button { showing.toggle() } label: {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 4) {
                    Image(systemName: "cpu")
                    Text(modelTitle)
                        .font(.system(size: LFChrome.captionSize))
                    Image(systemName: "chevron.down").font(.system(size: 8))
                }
                .fixedSize(horizontal: true, vertical: true)
                Image(systemName: "cpu")
            }
        }
        .help("识别模型：\(modelTitle)；采集中须先暂停再切换")
        .accessibilityLabel("识别模型 \(modelTitle)")
        .accessibilityIdentifier("model-selector")
        .popover(isPresented: $showing) {
            VStack(alignment: .leading, spacing: LFChrome.space12) {
                Text("识别模型").font(.headline)
                ForEach(["asr-small", "asr-large"], id: \.self) { id in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(id == "asr-small" ? "标准识别" : "高精度识别")
                                .fontWeight(.medium)
                            Text(workspace.hasModel(id) ? "已安装" : "未安装")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        if workspace.hasModel(id) {
                            Button(workspace.model == id ? "已选择" : "切换") {
                                workspace.selectRecognitionModel(id)
                            }
                            .disabled(workspace.model == id || live.isCapturing)
                        } else {
                            Button("下载") { Task { await workspace.downloadAndSelectModel(id) } }
                                .disabled(workspace.downloading)
                        }
                    }
                }
                Text(live.isCapturing ? "请先暂停，再切换识别模型。" : "高精度为可选下载，不承诺未经验证的质量优势。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(LFChrome.space16)
            .frame(width: 320)
        }
    }
}
