import AppKit
import LectureFlowCore
import SwiftUI

struct InstalledModel: Decodable, Identifiable, Sendable {
    let id: String
    let installed: Bool
    let source: String
    var name: String { id == "asr-small" ? "标准识别" : id == "asr-large" ? "高精度识别" : "中文翻译" }
}
struct ModelList: Decodable { let models: [InstalledModel] }
struct ModelDownloadProgress: Decodable {
    let model: String
    let completed_models: Int
    let total_models: Int
    let bytes_completed: Int
    let bytes_total: Int
}

struct SettingsView: View {
    @ObservedObject var workspace: Workspace
    @State private var message = ""
    @State private var devices: [InputDevice] = []
    @AppStorage("autoFloatingSubtitles") private var autoFloatingSubtitles = true
    @AppStorage("saveEnglishTranscript") private var saveEnglishTranscript = true
    @AppStorage("saveChineseTranslation") private var saveChineseTranslation = true

    var body: some View {
        Form {
            Section("本地模型") {
                ForEach(workspace.installedModels) { model in
                    HStack {
                        Text(model.name)
                        Spacer()
                        Text(model.installed ? "已就绪" : "未安装").foregroundStyle(model.installed ? .teal : .secondary)
                        if !model.installed {
                            Button("下载") { Task { await workspace.installModels([model.id]) } }
                                .disabled(workspace.downloading)
                        }
                    }
                }
                if workspace.downloading {
                    HStack { ProgressView().controlSize(.small); Text(workspace.downloadMessage)
                        Spacer(); Button("暂停下载") { workspace.cancelModelDownload() }
                    }
                    if let fraction = workspace.downloadFraction { ProgressView(value: fraction) }
                }
                Text("标准识别为默认。高精度识别可选下载，不承诺未经实测的质量优势。首次约 1.86 GB，可暂停继续；更新应用会复用已下载模型。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("记录方式细节") {
                Toggle("保存英文转写", isOn: $saveEnglishTranscript)
                Toggle("保存中文翻译", isOn: $saveChineseTranslation)
                Text("主界面用三种记录方式控制是否保存音频与是否显示字幕；以上细分项影响转写保存。切换记录方式或麦克风须先暂停。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("音频输入") {
                Picker("麦克风", selection: Binding(get: { workspace.live.inputDevice },
                                                    set: { workspace.live.inputDevice = $0 })) {
                    Text("跟随系统默认").tag(UInt32(0))
                    ForEach(devices) { device in Text(device.name).tag(device.id) }
                }.disabled(workspace.live.flags.needsMicrophone)
                Button("刷新设备") { devices = AudioDevices.inputs() }
                Text("暂停采集后可更换麦克风。只在你开启录音或字幕时采集声音。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("悬浮字幕") {
                Toggle("开始实时字幕时自动显示", isOn: $autoFloatingSubtitles)
            }
            Section("存储与关于") {
                Text(workspace.backend.paths.library.path).textSelection(.enabled).font(.caption)
                Button("在 Finder 中打开") { NSWorkspace.shared.open(workspace.backend.paths.library) }
                Text("本机单一资料库。备份前请暂停处理并完全退出，再复制整个资料库文件夹。测试可用环境变量注入隔离路径。")
                    .font(.caption).foregroundStyle(.secondary)
                Button("复制诊断信息") {
                    let text = [
                        "LectureFlow diagnostic",
                        "version: \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?")",
                        "models: \(workspace.installedModels.filter(\.installed).map(\.id).joined(separator: ","))",
                        "tasks: \(workspace.overview.courses.flatMap(\.lectures).flatMap(\.tasks).map { "\($0.state):\($0.id.prefix(8))" }.joined(separator: ","))",
                        "download: \(workspace.downloading ? "active" : "idle")",
                    ].joined(separator: "\n")
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                    message = "诊断信息已复制（不含音频、转写、路径细节与密钥）。"
                }
            }
            if !message.isEmpty { Text(message).font(.callout).foregroundStyle(.teal) }
        }.formStyle(.grouped).padding(10).frame(width: 610, height: 620)
            .task { devices = AudioDevices.inputs(); await workspace.refreshModels() }
            .onChange(of: workspace.live.inputDevice) { _, value in UserDefaults.standard.set(Int(value), forKey: "inputDevice") }
    }
}
