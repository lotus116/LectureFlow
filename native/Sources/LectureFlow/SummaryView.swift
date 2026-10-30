import AppKit
import SwiftUI

struct SummaryPreview: Decodable, Identifiable {
    let lecture_id: String
    let source_version: String?
    let materials: String
    let characters: Int
    let documents: [String]
    let digest: String
    var endpoint: String?
    var model: String?
    let summary_id: String?
    var id: String { digest }
}
struct SummaryEntry: Decodable, Identifiable {
    let id: String
    let state: String
    let model: String
    let output: String
    let error: String?
    let stale: Bool
    let progress: Int
    let total: Int
    let created_at: String
}
struct SummaryList: Decodable { let summaries: [SummaryEntry] }

struct SummaryView: View {
    @ObservedObject var workspace: Workspace
    let lecture: Lecture
    @State private var entries: [SummaryEntry] = []
    @State private var selected: String?
    @State private var preview: SummaryPreview?
    @State private var includeDocuments = true
    @State private var useLive = false
    @State private var working = false
    @Environment(\.openSettings) private var openSettings

    private var version: String? { useLive ? nil : workspace.activeTask?.version_id }
    private var entry: SummaryEntry? { entries.first { $0.id == selected } ?? entries.first }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Picker("来源", selection: $useLive) {
                    Text("当前转写版本").tag(false)
                    Text("课堂实时字幕").tag(true)
                }.frame(width: 250)
                Toggle("包含关联课件文字", isOn: $includeDocuments).toggleStyle(.checkbox)
                Spacer()
                Button("总结设置") { openSettings() }
            }
            HStack {
                Button("复制总结 Prompt") { Task { await copyPrompt() } }
                Button("导出材料与 Prompt") { Task { await exportPrompt() } }
                Spacer()
                Button("预览并生成总结") { Task { await prepare() } }
                    .buttonStyle(.borderedProminent).disabled(working)
            }
            Text("无 API 也可复制 Prompt 使用。转写尚未完成时，仅包含已处理的内容。")
                .font(.caption).foregroundStyle(.secondary)
            if !entries.isEmpty {
                Picker("笔记版本", selection: Binding(get: { entry?.id ?? "" }, set: { selected = $0 })) {
                    ForEach(Array(entries.enumerated()), id: \.element.id) { index, row in
                        Text("笔记 \(entries.count - index) · \(row.model) · \(label(row.state))").tag(row.id)
                    }
                }
            }
            if let row = entry {
                HStack {
                    Text(label(row.state)).font(.headline)
                    if row.stale { Text("素材已变化，可重新生成").foregroundStyle(.orange) }
                    if row.state == "running" { ProgressView().controlSize(.small); Text("\(row.progress)/\(row.total)").foregroundStyle(.secondary) }
                    Spacer()
                    if ["failed", "queued"].contains(row.state) {
                        Button("预览后继续") { Task { await review(row.id) } }
                    }
                    if !row.output.isEmpty {
                        Button("复制笔记") { copy(row.output) }
                        Button("打开导出文件") {
                            NSWorkspace.shared.open(URL(fileURLWithPath: lecture.path).appendingPathComponent("Summaries/\(row.id)"))
                        }
                    }
                }
                if let error = row.error { Text(error).font(.callout).foregroundStyle(.orange) }
                ScrollView { Text(row.output.isEmpty ? "笔记完成后会显示在这里。" : row.output)
                    .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled).lineSpacing(5) }
            } else {
                EmptyPanel(
                    title: "把课堂整理为学习笔记",
                    systemImage: "sparkles.rectangle.stack",
                    description: "选择材料并预览，或直接复制 Prompt 到你常用的 AI 服务。"
                )
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task(id: lecture.id) {
            while !Task.isCancelled { await reload(); try? await Task.sleep(for: .seconds(3)) }
        }
        .sheet(item: $preview) { material in
            VStack(alignment: .leading, spacing: 16) {
                Text("确认总结材料").font(.title2.bold())
                Text("服务：\(material.endpoint ?? "未配置")\n模型：\(material.model ?? "未配置")\n共 \(material.characters) 字；包含 \(material.documents.count) 份课件的提取文字。")
                    .font(.callout).textSelection(.enabled)
                Text("将发送下方转写与课件文字。不发送录音或课件原文件；使用你的服务账户。")
                    .font(.callout).foregroundStyle(.secondary)
                ScrollView { Text(String(material.materials.prefix(60_000)))
                    .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled) }
                if material.characters > 60_000 { Text("此处显示前 60,000 字；可复制完整材料查看。").font(.caption).foregroundStyle(.secondary) }
                HStack {
                    Button("复制完整材料") { copy(material.materials) }
                    Spacer()
                    Button("取消") { preview = nil }.keyboardShortcut(.cancelAction)
                    Button("确认发送并生成") { preview = nil; Task { await send(material) } }
                        .keyboardShortcut(.defaultAction)
                        .disabled((material.model ?? "").isEmpty)
                }
            }.padding(24).frame(width: 740, height: 610)
        }
    }

    private func arguments(_ action: String) -> [String] {
        ["summary", action, lecture.id] + (version.map { ["--version", $0] } ?? [])
    }
    private func prepare() async {
        working = true; defer { working = false }
        do {
            var args = arguments("preview")
            if !includeDocuments { args.append("--without-documents") }
            var item = try JSONDecoder().decode(SummaryPreview.self, from: await workspace.backend.call(args))
            item.endpoint = UserDefaults.standard.string(forKey: "summaryEndpoint") ?? "https://api.deepseek.com"
            item.model = UserDefaults.standard.string(forKey: "summaryModel") ?? ""
            preview = item
        } catch { workspace.error = error.localizedDescription }
    }
    private func review(_ id: String) async {
        do { preview = try JSONDecoder().decode(SummaryPreview.self, from: await workspace.backend.call(["summary", "review", id])) }
        catch { workspace.error = error.localizedDescription }
    }
    private func send(_ material: SummaryPreview) async {
        do {
            guard let endpoint = material.endpoint, let model = material.model else { return }
            let key = try SummaryKeychain.read(endpoint)
            guard !key.isEmpty else {
                workspace.error = "请先在总结设置中保存此服务的 API 密钥，或使用复制 Prompt。"; return
            }
            let id: String
            if let existing = material.summary_id { id = existing }
            else {
                var payload: [String: Any] = ["lecture_id": material.lecture_id, "digest": material.digest,
                    "endpoint": endpoint, "model": model, "include_documents": includeDocuments]
                if let source = material.source_version { payload["version_id"] = source }
                let input = try JSONSerialization.data(withJSONObject: payload)
                id = try JSONDecoder().decode(Created.self, from: await workspace.backend.call(["summary", "create"], input: input)).id
            }
            selected = id
            try await workspace.backend.startSummary(id, apiKey: key, digest: material.digest)
            await reload()
        } catch { workspace.error = error.localizedDescription }
    }
    private func copyPrompt() async {
        do {
            var args = arguments("preview")
            if !includeDocuments { args.append("--without-documents") }
            let data = try await workspace.backend.call(args)
            let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            if let prompt = payload?["prompt"] as? String { copy(prompt) }
        } catch { workspace.error = error.localizedDescription }
    }
    private func exportPrompt() async {
        do {
            var args = arguments("prompt")
            if !includeDocuments { args.append("--without-documents") }
            let result = try JSONDecoder().decode(Exported.self, from: await workspace.backend.call(args))
            NSWorkspace.shared.open(URL(fileURLWithPath: result.directory))
        } catch { workspace.error = error.localizedDescription }
    }
    private func reload() async {
        do { entries = try JSONDecoder().decode(SummaryList.self, from: await workspace.backend.call(["summary", "list", lecture.id])).summaries }
        catch { workspace.error = error.localizedDescription }
    }
    private func copy(_ text: String) { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string) }
    private func label(_ state: String) -> String {
        switch state { case "running": return "生成中"; case "completed": return "已完成"; case "failed": return "已中断"; default: return "等待生成" }
    }
}
