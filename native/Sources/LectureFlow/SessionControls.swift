import LectureFlowCore
import SwiftUI

struct SessionControlBar: View {
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    @State private var options = false
    private var active: Bool { workspace.session.state == .running || workspace.session.state == .preparing }

    var body: some View {
        VStack(alignment: .leading, spacing: LFChrome.space8) {
            HStack(alignment: .center, spacing: LFChrome.space12) {
                HStack(spacing: LFChrome.space10) {
                    statusDot
                    TimelineView(.periodic(from: .now, by: 0.5)) { _ in
                        Text(clock(workspace.session.duration(at: ProcessInfo.processInfo.systemUptime)))
                            .font(.system(size: 22, weight: .medium, design: .rounded).monospacedDigit())
                            .foregroundStyle(.primary)
                            .accessibilityLabel("采集计时")
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(status)
                            .font(.system(size: LFChrome.bodySize, weight: .semibold))
                        Text(audioHint)
                            .font(.system(size: LFChrome.captionSize))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    if live.isCapturing {
                        ProgressView(value: Double(live.level))
                            .frame(width: 44)
                            .tint(.red)
                            .help("麦克风实际输入音量")
                            .accessibilityLabel("麦克风输入音量")
                    }
                }

                Spacer(minLength: LFChrome.space8)

                HStack(spacing: LFChrome.space8) {
                    Button(workspace.session.primaryTitle, systemImage: active ? "pause.fill" : "record.circle.fill") {
                        workspace.primarySessionAction()
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .tint(active ? .orange : .red)
                    .disabled(!active && (workspace.busy || workspace.lecture == nil))
                    .accessibilityIdentifier("session-primary")

                    if workspace.session.hasSession {
                        Button {
                            workspace.endSession()
                        } label: {
                            Image(systemName: "stop.fill")
                        }
                        .controlSize(.large)
                        .help("结束 Lecture；不会删除已有内容")
                        .accessibilityLabel("结束 Lecture")
                        .accessibilityIdentifier("session-end")
                    }

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
            }

            if let notice = workspace.notice ?? secondaryDetail {
                Text(notice)
                    .font(.system(size: LFChrome.captionSize))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            if workspace.session.state == .running && workspace.session.desired.needsASR
                && !live.flags.needsASR && !workspace.captionsWaiting {
                Button("重新连接字幕") { workspace.reconnectCaptions() }
                    .font(.system(size: LFChrome.captionSize))
            }

            if workspace.session.state == .ended {
                HStack(spacing: LFChrome.space8) {
                    Label(
                        live.pendingSaves > 0 ? "正在写入资料库…" : "内容已保留",
                        systemImage: "checkmark.circle.fill"
                    )
                    .font(.system(size: LFChrome.captionSize))
                    .foregroundStyle(LFChrome.accent)
                    Spacer()
                    Button("继续录制") { workspace.primarySessionAction() }
                    Button("查看录音") { workspace.requestedTab = "audio" }
                    Button("生成课后转写") { Task { await workspace.enqueue() } }
                        .buttonStyle(.borderedProminent)
                        .tint(LFChrome.accent)
                        .disabled(workspace.lecture?.segments.isEmpty != false || workspace.busy || live.pendingSaves > 0)
                }
            }
        }
        .lfSoftPanelPadding()
    }

    private var statusDot: some View {
        Circle()
            .fill(active ? Color.red : (workspace.session.state == .paused ? Color.orange : LFChrome.accent.opacity(0.45)))
            .frame(width: 9, height: 9)
            .overlay {
                if active {
                    Circle()
                        .strokeBorder(Color.red.opacity(0.35), lineWidth: 3)
                        .frame(width: 15, height: 15)
                }
            }
            .accessibilityHidden(true)
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
        let value = Int(seconds)
        return String(format: "%02d:%02d:%02d", value / 3600, value / 60 % 60, value % 60)
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
    var body: some View {
        Button { showing.toggle() } label: {
            HStack(spacing: 4) {
                Image(systemName: "cpu")
                Text(workspace.model == "asr-large" ? "高精度" : "标准识别")
                    .font(.system(size: LFChrome.captionSize))
                Image(systemName: "chevron.down").font(.system(size: 8))
            }
        }
        .help("识别模型；采集中须先暂停再切换")
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
