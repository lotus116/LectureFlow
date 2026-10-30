import LectureFlowCore
import SwiftUI

struct LiveControlView: View {
    @ObservedObject var live: LiveController
    let lecture: Lecture
    let backend: Backend
    let model: String
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("课堂实时模式", systemImage: live.flags.needsMicrophone ? "mic.fill" : "mic")
                    .fontWeight(.medium).foregroundStyle(live.flags.needsMicrophone ? .teal : .primary)
                Spacer()
                Button(live.flags.needsMicrophone ? "全部暂停" : "全部开启") {
                    change(live.flags.needsMicrophone ? .paused : .all)
                }.buttonStyle(.borderedProminent)
            }
            HStack(spacing: 24) {
                Toggle("保存录音", isOn: binding(\.recording))
                Toggle("英文转写", isOn: binding(\.english))
                Toggle("中文翻译", isOn: binding(\.translate))
            }.toggleStyle(.switch).controlSize(.small)
            Text(live.message).font(.caption).foregroundStyle(.secondary)
            if !live.flags.recording && live.flags.needsASR {
                Text("仅处理字幕，不保存原始音频").font(.caption).foregroundStyle(.teal)
            }
        }.padding(16).background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12))
    }
    private func change(_ flags: CaptureFlags) {
        live.change(flags, lecture: lecture, backend: backend, model: model)
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
    var body: some View {
        ScrollViewReader { proxy in
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("临时字幕会随讲话更新；最近几句可修订").font(.caption).foregroundStyle(.secondary)
                    Spacer()
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
                        if let last = live.captions.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
                    }
                }
                if live.captions.isEmpty {
                    ContentUnavailableView("等待课堂讲话", systemImage: "waveform",
                        description: Text("开启英文转写或中文翻译后，字幕会出现在这里。"))
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 20) {
                            ForEach(live.captions) { caption in
                                VStack(alignment: .leading, spacing: 8) {
                                    HStack {
                                        Text(clock(caption.start)).monospacedDigit()
                                        Text(label(caption.status))
                                    }.font(.caption).foregroundStyle(.secondary)
                                    if !caption.chinese.isEmpty { Text(caption.chinese).font(.title3).lineSpacing(4) }
                                    if !caption.english.isEmpty { Text(caption.english).foregroundStyle(.secondary) }
                                }.frame(maxWidth: .infinity, alignment: .leading)
                                    .textSelection(.enabled).id(caption.id)
                                Divider()
                            }
                        }.padding(.vertical, 8)
                    }
                }
            }
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
