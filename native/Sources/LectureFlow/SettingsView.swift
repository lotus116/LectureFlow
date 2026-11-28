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
    @ThemePalette private var palette
    @AppStorage(LFTheme.preferenceKey) private var colorTheme = LFTheme.ice.rawValue
    @ObservedObject var workspace: Workspace
    @State private var message = ""
    @State private var devices: [InputDevice] = []
    @AppStorage("autoFloatingSubtitles") private var autoFloatingSubtitles = true
    @AppStorage("saveEnglishTranscript") private var saveEnglishTranscript = true
    @AppStorage(SourceLanguagePreference.key) private var sourceLanguage = "English"
    @AppStorage("accentReviewEnabled") private var accentReviewEnabled = false
    @AppStorage("saveChineseTranslation") private var saveChineseTranslation = true

    @State private var group = "外观"
    private let groups = ["外观", "识别与翻译", "音频", "存储"]

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                HStack {
                    Text("设置").font(.system(size: 20, weight: .semibold))
                    Spacer()
                    Text("LectureFlow").font(.caption).foregroundStyle(palette.secondaryText)
                }.padding(16)
                Divider()
                if geometry.size.width >= 700 {
                    HStack(spacing: 0) {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(groups, id: \.self) { item in
                                Button { group = item } label: {
                                    Text(item).frame(maxWidth: .infinity, alignment: .leading)
                                        .padding(.horizontal, 12).padding(.vertical, 10)
                                        .background(group == item ? palette.selection : .clear, in: RoundedRectangle(cornerRadius: 6))
                                }.buttonStyle(.plain).accessibilityAddTraits(group == item ? [.isSelected] : [])
                                .accessibilityIdentifier("settings-group-\(item)")
                            }
                            Spacer()
                        }.padding(8).frame(width: 164).background(palette.sidebar)
                        Divider()
                        settingsForm
                    }
                } else {
                    Picker("设置分组", selection: $group) {
                        ForEach(groups, id: \.self) { Text($0).tag($0) }
                    }.pickerStyle(.segmented).labelsHidden().padding(12)
                    settingsForm
                }
            }
        }
        .frame(minWidth: 540, idealWidth: 780, minHeight: 520, idealHeight: 620)
        .background(palette.canvas).foregroundStyle(palette.bodyText).tint(palette.accent)
            .task { devices = AudioDevices.inputs(); await workspace.refreshModels() }
            .onChange(of: workspace.live.inputDevice) { _, value in UserDefaults.standard.set(Int(value), forKey: "inputDevice") }
    }
    private var settingsForm: some View {
        Form {
            switch group {
            case "识别与翻译": modelFields; captionFields
            case "音频": audioFields
            case "存储": storageFields
            default: appearanceFields; floatingFields
            }
            if !message.isEmpty { Text(message).font(.callout).foregroundStyle(palette.saved) }
        }.formStyle(.grouped).scrollContentBackground(.hidden).padding(8)
    }

    private var appearanceFields: some View {
        Section("配色") {
                Picker("工作区配色", selection: Binding(
                    get: { LFTheme(preference: colorTheme).rawValue }, set: { colorTheme = $0 }
                )) {
                    ForEach(LFTheme.allCases) { theme in Text(theme.title).tag(theme.rawValue) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("settings-theme")
                HStack(spacing: 8) {
                    Text("即时生效，保留当前录音和阅读位置。")
                        .fixedSize(horizontal: false, vertical: true)
                        .font(.caption).foregroundStyle(palette.secondaryText)
                    Spacer()
                    Button("恢复默认配色") { colorTheme = LFTheme.ice.rawValue }
                        .disabled(colorTheme == LFTheme.ice.rawValue)
                        .accessibilityIdentifier("settings-theme-reset")
                }
            }
    }

    private var modelFields: some View {
        Section("本地模型") {
                ForEach(workspace.installedModels) { model in
                    HStack {
                        Text(model.name)
                        Spacer()
                        Text(model.installed ? "已就绪" : "未安装").foregroundStyle(model.installed ? palette.saved : .secondary)
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
                Text("记住上次选择；尚未保存偏好时默认高精度识别。模型下载可暂停继续，更新应用会复用已下载模型。识别质量仍需按实际课堂验证。")
                    .font(.caption).foregroundStyle(palette.secondaryText)
            }
    }

    private var captionFields: some View {
        Section("记录方式细节") {
                Picker("新任务讲话语言", selection: $sourceLanguage) {
                    Text("英语课堂（推荐）").tag("English")
                    Text("自动检测所有语言（实验）").tag("auto")
                }
                .disabled(workspace.live.isCapturing)
                Text("英语课堂默认固定英语。自动模式可能将重口音误判为其他语言。用于新会话和新课后任务，已有会话沿用原配置。")
                    .font(.caption).foregroundStyle(palette.secondaryText)
                Toggle("口音增强（实验）", isOn: $accentReviewEnabled)
                    .disabled(workspace.live.isCapturing)
                Text("默认关闭，仅固定英语生效。暂停后切换，继续采集时生效；本地复核原音，证据不足保留原字幕。可能增加字幕延迟。仅字幕模式不保存音频。")
                    .font(.caption).foregroundStyle(palette.secondaryText)
                Toggle("保存原文转写", isOn: $saveEnglishTranscript)
                Toggle("保存中文翻译", isOn: $saveChineseTranslation)
                Text("主界面用三种记录方式控制是否保存音频与是否显示字幕；以上细分项影响转写保存。切换记录方式或麦克风须先暂停。")
                    .font(.caption).foregroundStyle(palette.secondaryText)
            }
    }

    private var audioFields: some View {
        Section("音频输入") {
                Picker("麦克风", selection: Binding(get: { workspace.live.inputDevice },
                                                    set: { workspace.live.inputDevice = $0 })) {
                    Text("跟随系统默认").tag(UInt32(0))
                    ForEach(devices) { device in Text(device.name).tag(device.id) }
                }.disabled(workspace.live.flags.needsMicrophone)
                Button("刷新设备") { devices = AudioDevices.inputs() }
                Text("暂停采集后可更换麦克风。只在你开启录音或字幕时采集声音。")
                    .font(.caption).foregroundStyle(palette.secondaryText)
            }
    }

    private var floatingFields: some View {
        Section("悬浮字幕") {
                Toggle("开始实时字幕时自动显示", isOn: $autoFloatingSubtitles)
            }
    }

    private var storageFields: some View {
        Section("存储与关于") {
                Text(workspace.backend.paths.library.path).textSelection(.enabled).font(.caption)
                Button("在 Finder 中打开") { NSWorkspace.shared.open(workspace.backend.paths.library) }
                Text("本机单一资料库。备份前请暂停处理并完全退出，再复制整个资料库文件夹。测试可用环境变量注入隔离路径。")
                    .font(.caption).foregroundStyle(palette.secondaryText)
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
    }

}
