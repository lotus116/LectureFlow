import LectureFlowCore
import SwiftUI

struct SessionControlBar: View {
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    @State private var options = false
    @State private var cancelling = false
    private var active: Bool { workspace.session.state == .running || workspace.session.state == .preparing }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Circle().fill(active ? Color.red : Color.secondary.opacity(0.35)).frame(width: 8, height: 8)
                TimelineView(.periodic(from: .now, by: 0.5)) { _ in
                    Text(clock(workspace.session.duration(at: ProcessInfo.processInfo.systemUptime)))
                        .font(.system(size: 25, weight: .medium, design: .rounded)).monospacedDigit()
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text(status).font(.callout.weight(.medium))
                    Text(workspace.session.modeTitle).font(.caption2).foregroundStyle(.secondary)
                }
                if live.isCapturing {
                    ProgressView(value: Double(live.level)).frame(width: 58).tint(.red)
                        .help("麦克风实际输入音量").accessibilityLabel("麦克风输入音量")
                }
                Spacer(minLength: 4)
                Button(workspace.session.primaryTitle, systemImage: active ? "pause.fill" : "record.circle") {
                    workspace.primarySessionAction()
                }.buttonStyle(.borderedProminent).controlSize(.regular)
                    .tint(active ? .orange : .red)
                    .disabled(!active && (workspace.busy || workspace.lecture == nil))
                    .accessibilityIdentifier("session-primary")
                if workspace.session.hasSession {
                    Button { workspace.endSession() } label: { Image(systemName: "stop.fill") }
                        .controlSize(.regular).help("保存并结束；不会删除录音或字幕")
                        .accessibilityLabel("保存并结束").accessibilityIdentifier("session-end")
                }
                Button { options.toggle() } label: { Image(systemName: "slider.horizontal.3") }
                    .help("记录方式").popover(isPresented: $options) {
                        CaptureOptionsView(workspace: workspace)
                    }
            }
            HStack(alignment: .top, spacing: 10) {
                Text(workspace.notice ?? detail).font(.caption).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                ModelSelector(workspace: workspace, live: live)
            }
            if workspace.session.state == .running && workspace.session.desired.needsASR
                && !live.flags.needsASR && !workspace.captionsWaiting {
                Button("重新连接字幕（录音继续）") { workspace.reconnectCaptions() }.font(.caption)
            }
            if workspace.session.state == .ended {
                HStack {
                    Label(live.pendingSaves > 0 ? "正在加入资料库…" : "已有内容保留在本 Lecture", systemImage: "checkmark.circle")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("继续录制") { workspace.primarySessionAction() }
                    Button("查看录音") { workspace.requestedTab = "audio" }
                    Button("生成课后转写") { Task { await workspace.enqueue() } }
                        .disabled(workspace.lecture?.segments.isEmpty != false || workspace.busy || live.pendingSaves > 0)
                }
            }
        }
        .lfCardPadding()
    }
    private var status: String {
        switch workspace.session.state {
        case .idle: return "准备好，就开始"
        case .preparing: return "正在打开麦克风…"
        case .running: return live.flags.recording ? "正在录音" : "字幕进行中 · 不保存录音"
        case .paused: return "已暂停"
        case .ended: return "本次已结束"
        }
    }
    private var detail: String {
        if live.pendingSaves > 0 { return "录音文件已封存，正在加入本 Lecture。" }
        if active { return workspace.captionsWaiting ? "录音已开始，字幕等待本地模型就绪。" : live.message }
        return "开始后自动保存；暂停可继续；结束 Lecture 后仍可追加录制。"
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
        VStack(alignment: .leading, spacing: 14) {
            Text("记录方式").font(.headline)
            ForEach(CapturePreset.allCases, id: \.self) { preset in
                Button { workspace.configureSession(preset.flags) } label: {
                    HStack {
                        Image(systemName: workspace.session.desired == preset.flags ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(.tint)
                        Text(preset.title); Spacer()
                    }.padding(.vertical, 3)
                }.buttonStyle(.plain).disabled(!workspace.session.canConfigure)
            }
            if !workspace.session.canConfigure {
                Text("先暂停，再调整记录方式；录音会保存在同一堂课。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Divider()
            DisclosureGroup("更多选项") {
                VStack(alignment: .leading, spacing: 10) {
                    Toggle("保存原始录音", isOn: binding(\.recording))
                    Toggle("保存英文转写", isOn: binding(\.english))
                    Toggle("翻译为中文", isOn: binding(\.translate))
                    Text("关闭英文保存仍可翻译；关闭原始录音后，仅保留已开启的字幕内容。")
                        .font(.caption2).foregroundStyle(.secondary)
                }.toggleStyle(.checkbox).padding(.top, 8).disabled(!workspace.session.canConfigure)
                if workspace.live.flags.recording {
                    Button("取消当前录音段", role: .destructive) { cancelling = true }
                        .padding(.top, 10)
                }
            }
        }.padding(20).frame(width: 310)
            .confirmationDialog("取消当前录音段？录音会移入可恢复的废纸篓，字幕按原设置继续。", isPresented: $cancelling) {
                Button("取消当前录音段", role: .destructive) { workspace.live.cancelCurrentRecording() }
            }
    }
    private func binding(_ key: WritableKeyPath<CaptureFlags, Bool>) -> Binding<Bool> {
        Binding(get: { workspace.session.desired[keyPath: key] }, set: { value in
            var flags = workspace.session.desired; flags[keyPath: key] = value
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
                Image(systemName: live.pendingModel == nil ? "cpu" : "arrow.trianglehead.2.clockwise")
                Text(workspace.model == "asr-large" ? "大模型" : "标准模型")
                Image(systemName: "chevron.down").font(.system(size: 8))
            }
        }.help("选择识别模型，或下载尚未安装的模型").accessibilityIdentifier("model-selector")
            .popover(isPresented: $showing) {
                VStack(alignment: .leading, spacing: 14) {
                    Text("识别模型").font(.headline)
                    ForEach(["asr-small", "asr-large"], id: \.self) { id in
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(id == "asr-small" ? "标准 · 0.6B" : "大模型 · 1.7B").fontWeight(.medium)
                                Text(workspace.hasModel(id) ? "已安装，可切换" : "尚未安装，需要先下载")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if workspace.hasModel(id) {
                                Button(workspace.model == id ? "已选择" : "切换") { workspace.selectRecognitionModel(id) }
                                    .disabled(workspace.model == id)
                            } else {
                                Button("下载并切换") { Task { await workspace.downloadAndSelectModel(id) } }
                                    .disabled(workspace.downloading)
                            }
                        }
                    }
                    Divider()
                    if workspace.downloading { Text(workspace.downloadMessage).font(.caption).foregroundStyle(.secondary) }
                    Text(live.pendingModel == nil ? "课堂中切换会等待当前语音片段结束，录音继续保存。" : "正在加载或切换；生效后显示新的模型状态，录音不受影响。")
                        .font(.caption).foregroundStyle(.secondary)
                    if let active = live.activeModel {
                        Text("当前生效：\(active == "asr-large" ? "大模型 · 1.7B" : "标准 · 0.6B")")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(20).frame(width: 350)
            }
    }
}
