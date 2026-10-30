import AppKit
import LectureFlowCore
import SwiftUI

struct LiveControlView: View {
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    let lecture: Lecture
    @State private var cancelling = false
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 14) {
                Image(systemName: live.flags.needsMicrophone ? "waveform" : "mic")
                    .foregroundStyle(live.flags.needsMicrophone ? .teal : .secondary)
                Toggle("录音", isOn: binding(\.recording))
                Toggle("英文", isOn: binding(\.english))
                Toggle("翻译", isOn: binding(\.translate))
                Spacer()
                Button(live.flags.needsMicrophone ? "暂停课堂" : "开始课堂", systemImage: live.flags.needsMicrophone ? "pause.fill" : "play.fill") {
                    change(live.flags.needsMicrophone ? .paused : .all)
                }.buttonStyle(.borderedProminent)
                Menu {
                    Picker("识别模型", selection: $workspace.model) {
                        Text("轻快").tag("asr-small").disabled(!workspace.hasModel("asr-small"))
                        Text("高精度").tag("asr-large").disabled(!workspace.hasModel("asr-large"))
                    }
                    Button("取消本段录音", role: .destructive) { cancelling = true }.disabled(!live.flags.recording)
                } label: { Image(systemName: "chevron.down") }.menuStyle(.borderlessButton).frame(width: 18)
            }.toggleStyle(.switch).controlSize(.mini)
            HStack {
                Text(live.flags.needsMicrophone ? live.message : "录音、英文和翻译可独立开关")
                Spacer()
                Text("全程本机处理")
            }.font(.caption2).foregroundStyle(.secondary)
        }.padding(10).background(.quaternary.opacity(0.25), in: RoundedRectangle(cornerRadius: 7))
            .disabled(workspace.busy)
            .confirmationDialog("取消当前录音段？字幕按现有开关继续；录音会移入可恢复的废纸篓。", isPresented: $cancelling) {
                Button("取消本段录音", role: .destructive) { live.cancelCurrentRecording() }
            }
    }
    private func change(_ flags: CaptureFlags) {
        workspace.changeCapture(flags)
    }
    private func binding(_ key: WritableKeyPath<CaptureFlags, Bool>) -> Binding<Bool> {
        Binding(get: { live.flags[keyPath: key] }, set: { value in
            var flags = live.flags; flags[keyPath: key] = value; change(flags)
        })
    }
}

struct LiveTranscriptView: View {
    @ObservedObject var live: LiveController
    let lecture: Lecture
    let backend: Backend
    @State private var search = ""
    @State private var language = "both"
    @State private var follow = true
    private var visible: [LiveCaption] {
        live.captions.filter { search.isEmpty || $0.english.localizedCaseInsensitiveContains(search) || $0.chinese.contains(search) }
    }
    private var updateKey: String { live.captions.last.map { "\($0.id):\($0.revision)" } ?? "" }
    var body: some View {
        ScrollViewReader { proxy in
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Picker("显示", selection: $language) {
                        Text("双语").tag("both"); Text("中文").tag("zh"); Text("英文").tag("en")
                    }.frame(width: 115)
                    TextField("搜索字幕", text: $search).textFieldStyle(.roundedBorder).frame(maxWidth: 190)
                    Spacer()
                    Button("复制") {
                        let text = visible.map { caption in
                            [clock(caption.start), language == "en" ? "" : caption.chinese,
                             language == "zh" ? "" : caption.english].filter { !$0.isEmpty }.joined(separator: "\n")
                        }.joined(separator: "\n\n")
                        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string)
                    }.disabled(visible.isEmpty)
                    Button("导出实时字幕") {
                        Task {
                            do {
                                let data = try await backend.call(["live", "export", lecture.id])
                                let result = try JSONDecoder().decode(Exported.self, from: data)
                                NSWorkspace.shared.open(URL(fileURLWithPath: result.directory))
                            } catch { live.error = error.localizedDescription }
                        }
                    }.disabled(live.captions.isEmpty)
                    Button("返回最新") {
                        follow = true; search = ""
                        DispatchQueue.main.async { withAnimation { proxy.scrollTo("latest-caption", anchor: .bottom) } }
                    }
                }
                Text(follow ? "跟随最新 · 临时字幕会更新，最近几句可修订" : "正在查看历史 · 点击“返回最新”继续跟随")
                    .font(.caption).foregroundStyle(.secondary)
                if live.captions.isEmpty {
                    ContentUnavailableView("等待课堂讲话", systemImage: "waveform",
                        description: Text("开启英文转写或中文翻译后，字幕会出现在这里。"))
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 20) {
                            ForEach(visible) { caption in
                                VStack(alignment: .leading, spacing: 8) {
                                    HStack {
                                        Text(clock(caption.start)).monospacedDigit()
                                        Text(label(caption.status))
                                    }.font(.caption).foregroundStyle(.secondary)
                                    if language != "en" && !caption.chinese.isEmpty { Text(caption.chinese).font(.title3).lineSpacing(4) }
                                    if language != "zh" && !caption.english.isEmpty { Text(caption.english).foregroundStyle(.secondary) }
                                }.frame(maxWidth: .infinity, alignment: .leading)
                                    .textSelection(.enabled).id(caption.id)
                                Divider()
                            }
                            Color.clear.frame(height: 1).id("latest-caption")
                        }.padding(.vertical, 8)
                    }.background(ScrollActivityObserver { follow = false })
                }
            }
            .onChange(of: updateKey) { _, _ in
                if follow && search.isEmpty { proxy.scrollTo("latest-caption", anchor: .bottom) }
            }
            .onChange(of: search) { _, text in if !text.isEmpty { follow = false } }
        }
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
