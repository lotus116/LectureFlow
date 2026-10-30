import AppKit
import LectureFlowCore
import Security
import SwiftUI

enum SummaryKeychain {
    private static func query(_ endpoint: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "local.lectureflow.summary",
         kSecAttrAccount as String: endpoint.trimmingCharacters(in: CharacterSet(charactersIn: "/"))]
    }
    static func read(_ endpoint: String) throws -> String {
        var request = query(endpoint)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return "" }
        guard status == errSecSuccess, let data = result as? Data,
              let key = String(data: data, encoding: .utf8) else { throw failure(status) }
        return key
    }
    static func save(_ key: String, endpoint: String) throws {
        let data = Data(key.utf8)
        let status = SecItemUpdate(query(endpoint) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var item = query(endpoint)
            item[kSecValueData as String] = data
            item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            let added = SecItemAdd(item as CFDictionary, nil)
            if added != errSecSuccess { throw failure(added) }
        } else if status != errSecSuccess { throw failure(status) }
    }
    static func remove(_ endpoint: String) throws {
        let status = SecItemDelete(query(endpoint) as CFDictionary)
        if status != errSecSuccess && status != errSecItemNotFound { throw failure(status) }
    }
    private static func failure(_ status: OSStatus) -> Error {
        BackendError.unavailable("无法访问系统钥匙串（\(status)），请检查系统提示后重试。")
    }
}

struct InstalledModel: Decodable, Identifiable, Sendable {
    let id: String
    let installed: Bool
    let source: String
    var name: String { id == "asr-small" ? "轻快识别" : id == "asr-large" ? "高精度识别" : "中文翻译" }
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
    @State private var endpoint = UserDefaults.standard.string(forKey: "summaryEndpoint") ?? "https://api.deepseek.com"
    @State private var summaryModel = UserDefaults.standard.string(forKey: "summaryModel") ?? ""
    @State private var key = ""
    @State private var message = ""
    @State private var devices: [InputDevice] = []

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
                Text("首次打开自动后台下载默认模型，约 1.86 GB；可暂停和继续。下载不读取课堂资料，更新应用会复用已下载模型。")
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
            Section("课堂总结（可选）") {
                TextField("服务地址", text: $endpoint)
                TextField("模型名称", text: $summaryModel)
                SecureField("API 密钥（留空保留原密钥）", text: $key)
                HStack {
                    Button("保存总结设置") {
                        do {
                            let address = endpoint.trimmingCharacters(in: .whitespacesAndNewlines)
                            guard let url = URLComponents(string: address), url.scheme == "https",
                                  url.host != nil, url.user == nil, url.password == nil,
                                  url.query == nil, url.fragment == nil else {
                                message = "请填写不含密钥或查询参数的 HTTPS 服务地址。"; return
                            }
                            if !key.isEmpty { try SummaryKeychain.save(key.trimmingCharacters(in: .whitespacesAndNewlines), endpoint: address) }
                            UserDefaults.standard.set(address, forKey: "summaryEndpoint")
                            UserDefaults.standard.set(summaryModel.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "summaryModel")
                            key = ""; message = "设置已保存；密钥保存在 macOS 钥匙串。"
                        } catch { message = error.localizedDescription }
                    }
                    Button("移除密钥") {
                        do { try SummaryKeychain.remove(endpoint); message = "密钥已移除。" }
                        catch { message = error.localizedDescription }
                    }
                }
                Text("填写兼容 Chat Completions 的服务地址和模型名。仅在总结页预览并确认发送后调用；无密钥时可复制 Prompt。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("资料库") {
                Text(workspace.backend.paths.library.path).textSelection(.enabled).font(.caption)
                HStack {
                    Button("在 Finder 中打开") { NSWorkspace.shared.open(workspace.backend.paths.library) }
                    Button("选择其他资料库…") { chooseLibrary() }.disabled(workspace.live.flags.needsMicrophone)
                }
                Text("选择文件夹后，下次完全退出并重新打开应用时生效；已有资料不会自动移动。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !message.isEmpty { Text(message).font(.callout).foregroundStyle(.teal) }
        }.formStyle(.grouped).padding(10).frame(width: 610, height: 710)
            .task { devices = AudioDevices.inputs(); await workspace.refreshModels() }
            .onChange(of: workspace.live.inputDevice) { _, value in UserDefaults.standard.set(Int(value), forKey: "inputDevice") }
    }

    private func chooseLibrary() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.prompt = "使用此文件夹"; panel.message = "选择包含 Library.sqlite3 的资料库，或选择空文件夹建立新资料库。"
        if panel.runModal() == .OK, let url = panel.url {
            UserDefaults.standard.set(url.path, forKey: "libraryPath")
            message = "资料库位置已保存。请完全退出并重新打开应用。"
        }
    }
}
