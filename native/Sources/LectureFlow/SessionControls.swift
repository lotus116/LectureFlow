import Combine
import LectureFlowCore
import SwiftUI

struct SessionControlBar: View {
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    let showingAudio: Bool
    @State private var options = false

    private var active: Bool {
        workspace.session.state == .running || workspace.session.state == .preparing
    }

    private var paused: Bool { workspace.session.state == .paused }
    private var inlineNotice: String? { workspace.notice ?? secondaryDetail }

    var body: some View {
        VStack(alignment: .leading, spacing: LFChrome.space10) {
            recordingIsland
            metaRow

            if workspace.session.state == .running && workspace.session.desired.needsASR
                && !live.flags.needsASR && !workspace.captionsWaiting {
                Button("重新连接字幕") { workspace.reconnectCaptions() }
                    .font(.system(size: LFChrome.captionSize))
            }

            if workspace.session.state == .ended {
                endedRow
            }
        }
        .lfSoftPanelPadding()
    }

    /// Waveform fills free width; timer and transport never wrap.
    private var recordingIsland: some View {
        HStack(alignment: .center, spacing: 12) {
            RecordingWaveformStrip(
                level: live.level,
                active: live.isCapturing || active,
                paused: paused
            )
            .frame(minWidth: 64, maxWidth: .infinity)
            .frame(height: 32)
            .layoutPriority(0)

            TimelineView(.periodic(from: .now, by: 0.5)) { _ in
                Text(clock(workspace.session.duration(at: ProcessInfo.processInfo.systemUptime)))
                    .font(.system(size: 20, weight: .medium, design: .rounded).monospacedDigit())
                    .foregroundStyle(.primary)
                    .fixedSize(horizontal: true, vertical: true)
                    .accessibilityLabel("采集计时")
            }
            .layoutPriority(2)

            HStack(spacing: 8) {
                transportPrimary
                if workspace.session.hasSession {
                    transportStop
                }
            }
            .fixedSize(horizontal: true, vertical: false)
            .layoutPriority(3)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(Color.primary.opacity(0.04))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.06), lineWidth: 1)
        )
    }

    private var transportPrimary: some View {
        Button {
            workspace.primarySessionAction()
        } label: {
            // Avoid SF Symbol record.circle.fill + extra circle (looks like a huge double ring).
            ZStack {
                Circle()
                    .fill(Color.primary.opacity(0.07))
                    .frame(width: 32, height: 32)
                switch workspace.session.state {
                case .idle, .ended:
                    Circle()
                        .fill(Color.red)
                        .frame(width: 12, height: 12)
                case .running, .preparing:
                    Image(systemName: "pause.fill")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.primary)
                case .paused:
                    Image(systemName: "play.fill")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.primary)
                        .offset(x: 1)
                }
            }
            .frame(width: 32, height: 32)
        }
        .buttonStyle(.plain)
        .disabled(!active && !paused && (workspace.busy || workspace.lecture == nil))
        .help(workspace.session.primaryTitle)
        .accessibilityLabel(workspace.session.primaryTitle)
        .accessibilityIdentifier("session-primary")
    }

    private var transportStop: some View {
        Button {
            workspace.endSession()
        } label: {
            ZStack {
                Circle()
                    .strokeBorder(Color.primary.opacity(0.5), lineWidth: 2.5)
                    .frame(width: 32, height: 32)
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(Color(red: 1.0, green: 0.42, blue: 0.18))
                    .frame(width: 11, height: 11)
            }
            .frame(width: 32, height: 32)
        }
        .buttonStyle(.plain)
        .help("结束 Lecture；不会删除已有内容")
        .accessibilityLabel("结束 Lecture")
        .accessibilityIdentifier("session-end")
    }

    /// Status + notice flex; tools stay usable. Stacks when the row is too tight.
    private var metaRow: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: LFChrome.space8) {
                statusCluster.fixedSize(horizontal: true, vertical: true)
                flexibleDetail
                metaTools.layoutPriority(2)
            }
            .frame(minWidth: 300)

            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .center, spacing: LFChrome.space8) {
                    statusCluster.fixedSize(horizontal: true, vertical: true)
                    flexibleDetail
                }
                metaTools
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
    }

    private var statusCluster: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(statusColor)
                .frame(width: 7, height: 7)
            Text(status)
                .font(.system(size: LFChrome.captionSize, weight: .semibold))
                .foregroundStyle(.primary)
                .lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }

    private var flexibleDetail: some View {
        HStack(spacing: 6) {
            Text("·")
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)
            Text(audioHint)
                .font(.system(size: LFChrome.captionSize))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.tail)
            if let notice = inlineNotice {
                Text("·")
                    .foregroundStyle(.tertiary)
                    .accessibilityHidden(true)
                Text(notice)
                    .font(.system(size: LFChrome.captionSize))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .help(notice)
            }
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

    private var endedRow: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: LFChrome.space8) {
                endedStatusLabel
                Spacer(minLength: 8)
                endedActions
            }
            VStack(alignment: .leading, spacing: LFChrome.space8) {
                endedStatusLabel
                endedActions
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
    }

    private var endedStatusLabel: some View {
        Label(
            live.pendingSaves > 0 ? "正在写入资料库…" : "内容已保留",
            systemImage: "checkmark.circle.fill"
        )
        .font(.system(size: LFChrome.captionSize))
        .foregroundStyle(LFChrome.accent)
        .lineLimit(1)
        .fixedSize(horizontal: true, vertical: true)
    }

    private var endedActions: some View {
        HStack(spacing: LFChrome.space8) {
            Button("继续录制") { workspace.primarySessionAction() }
            if !showingAudio {
                Button("查看录音") { workspace.requestedTab = "audio" }
                Button("生成课后转写") { Task { await workspace.enqueue() } }
                    .buttonStyle(.borderedProminent)
                    .tint(LFChrome.accent)
                    .disabled(workspace.lecture?.segments.isEmpty != false || workspace.busy || live.pendingSaves > 0)
            }
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    private var statusColor: Color {
        if active { return .red }
        if paused { return .orange }
        return LFChrome.accent.opacity(0.55)
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
            let count = max(16, Int((geo.size.width + barSpacing) / (barWidth + barSpacing)))
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
                    .fill(Color(red: 1.0, green: 0.42, blue: 0.18))
                    .frame(width: 2, height: geo.size.height * 0.92)
                    .offset(x: playheadX - 1)
                    .accessibilityHidden(true)
            }
            .onChange(of: count) { _, newCount in
                resizeBuffer(to: newCount)
            }
            .onAppear { resizeBuffer(to: count) }
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
    @ObservedObject var workspace: Workspace
    @State private var cancelling = false
    var body: some View {
        VStack(alignment: .leading, spacing: LFChrome.space12) {
            Text("记录方式").font(.headline)
            ForEach(CapturePreset.allCases, id: \.self) { preset in
                Button { workspace.configureSession(preset.flags) } label: {
                    HStack {
                        Image(systemName: workspace.session.desired == preset.flags ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(LFChrome.accent)
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
                    Toggle("保存英文转写", isOn: binding(\.english))
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
