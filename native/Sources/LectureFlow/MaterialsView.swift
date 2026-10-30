import AppKit
import LectureFlowCore
import SwiftUI
import UniformTypeIdentifiers

struct MaterialList: Decodable {
    var documents: [CourseDocument] = []
    var terms: [GlossaryTerm] = []
}
struct CourseDocument: Decodable, Identifiable {
    let id: String
    let original_name: String
    let page_count: Int
    let path: String
    let preview: String
}
struct GlossaryTerm: Decodable, Identifiable {
    let id: String
    let english: String
    let chinese: String
    let confirmed: Int
    let sources: String
}

struct MaterialsView: View {
    @ObservedObject var workspace: Workspace
    let kind: String
    let ownerID: String
    @State private var materials = MaterialList()
    @State private var importing = false
    @State private var working = false
    @State private var progress = ""
    @State private var search = ""
    @State private var editID: String?
    @State private var english = ""
    @State private var chinese = ""
    @State private var editing = false
    @State private var removing: CourseDocument?
    @State private var previewing: CourseDocument?
    @State private var forceOCR = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text(kind == "course" ? "课程共享课件" : "本堂课课件").font(.title2.bold())
                Spacer()
                Toggle("PDF 强制 OCR", isOn: $forceOCR).toggleStyle(.checkbox)
                    .help("扫描件会自动识别文字；图文混合课件可勾选后重新导入。")
                Button("导入课件") { importing = true }.disabled(working)
            }
            Text("课件留在本机。确认的术语用于后续识别和翻译；实时模式重新开启后使用新术语。")
                .font(.callout).foregroundStyle(.secondary)
            if kind == "lecture" {
                Text("所属课程的共享课件与已确认术语也会自动应用。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if working { HStack { ProgressView().controlSize(.small); Text(progress) }.font(.callout) }
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(materials.documents) { document in
                        HStack {
                            Image(systemName: "doc.text").foregroundStyle(.teal)
                            Text(document.original_name)
                            Text("\(document.page_count) 页").foregroundStyle(.secondary)
                            Spacer()
                            Button("原文件") { NSWorkspace.shared.open(URL(fileURLWithPath: document.path)) }
                            Button("提取文字") { previewing = document }
                            Button(role: .destructive) { removing = document } label: { Image(systemName: "minus.circle") }
                        }
                    }
                    if materials.documents.isEmpty { Text("导入 PDF、PPTX 或 PPT，让术语与课程内容关联。").foregroundStyle(.secondary) }
                    Divider()
                    HStack {
                        Text("术语表").font(.headline)
                        Text("\(materials.terms.filter { $0.confirmed == 1 }.count) 已确认 / \(materials.terms.count) 候选")
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        TextField("筛选术语", text: $search).textFieldStyle(.roundedBorder).frame(width: 180)
                        Button("添加术语") { editID = nil; english = ""; chinese = ""; editing = true }
                    }
                    Text("自动提取的是候选词；核对拼写后勾选使用，可补充中文对照。")
                        .font(.caption).foregroundStyle(.secondary)
                    LazyVStack(alignment: .leading, spacing: 8) {
                        ForEach(materials.terms.filter { search.isEmpty || $0.english.localizedCaseInsensitiveContains(search) || $0.chinese.contains(search) }) { term in
                            HStack {
                                Toggle("", isOn: Binding(get: { term.confirmed == 1 }, set: { value in
                                    Task { await saveTerm(term.id, english: term.english, chinese: term.chinese, confirmed: value) }
                                })).labelsHidden().toggleStyle(.checkbox)
                                Text(term.english).frame(maxWidth: .infinity, alignment: .leading)
                                Text(term.chinese.isEmpty ? "未填写中文" : term.chinese)
                                    .foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
                                Button("编辑") { editID = term.id; english = term.english; chinese = term.chinese; editing = true }
                            }.padding(.vertical, 3)
                        }
                    }
                }.padding(.vertical, 8)
            }
        }
        .task(id: ownerID) { await reload() }
        .fileImporter(isPresented: $importing, allowedContentTypes: [
            .pdf, UTType(filenameExtension: "pptx") ?? .data, UTType(filenameExtension: "ppt") ?? .data
        ], allowsMultipleSelection: true) { result in
            switch result {
            case .success(let urls): Task { await importFiles(urls) }
            case .failure(let error): workspace.error = error.localizedDescription
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            let supported = urls.filter { ["pdf", "ppt", "pptx"].contains($0.pathExtension.lowercased()) }
            guard !supported.isEmpty, !working else { return false }
            Task { await importFiles(supported) }; return true
        }
        .sheet(isPresented: $editing) {
            VStack(alignment: .leading, spacing: 18) {
                Text("确认术语").font(.title2.bold())
                TextField("英文术语或缩写", text: $english).textFieldStyle(.roundedBorder)
                TextField("中文对照（可留空）", text: $chinese).textFieldStyle(.roundedBorder)
                HStack { Spacer(); Button("取消") { editing = false }
                    Button("保存并使用") {
                        let key = editID, en = english, zh = chinese
                        editing = false
                        Task { await saveTerm(key, english: en, chinese: zh, confirmed: true) }
                    }.keyboardShortcut(.defaultAction).disabled(english.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }.padding(24).frame(width: 420)
        }
        .sheet(item: $previewing) { document in
            VStack(alignment: .leading) {
                HStack { Text(document.original_name).font(.headline); Spacer(); Button("完成") { previewing = nil } }
                ScrollView { Text(document.preview).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled) }
            }.padding(24).frame(width: 720, height: 540)
        }
        .confirmationDialog("移除“\(removing?.original_name ?? "")”？其历史文件仍会保留，后续处理将不再使用。", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button("移除课件", role: .destructive) {
                if let item = removing {
                    Task { await workspace.perform(["materials", "remove", item.id, "--confirmed"]); await reload() }
                }
                removing = nil
            }
        }
    }

    private func reload() async {
        do { materials = try JSONDecoder().decode(MaterialList.self, from: await workspace.backend.call(["materials", "list", kind, ownerID])) }
        catch { workspace.error = error.localizedDescription }
    }

    private func importFiles(_ urls: [URL]) async {
        working = true; defer { working = false }
        for (index, url) in urls.enumerated() {
            let accessed = url.startAccessingSecurityScopedResource()
            defer { if accessed { url.stopAccessingSecurityScopedResource() } }
            progress = "正在导入 \(index + 1)/\(urls.count)：\(url.lastPathComponent)"
            do {
                var arguments = ["materials", "import", kind, ownerID, url.path]
                let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("lectureflow-pages-\(UUID().uuidString).json")
                defer { try? FileManager.default.removeItem(at: temporary) }
                if url.pathExtension.lowercased() == "pdf" {
                    let ocr = forceOCR
                    let extraction = try await Task.detached(priority: .userInitiated) {
                        try DocumentExtractor.pdf(url, forceOCR: ocr)
                    }.value
                    try JSONEncoder().encode(extraction).write(to: temporary, options: .atomic)
                    arguments += ["--pages-file", temporary.path]
                }
                _ = try await workspace.backend.call(arguments)
            } catch { workspace.error = "\(url.lastPathComponent)：\(error.localizedDescription)"; break }
        }
        await reload(); await workspace.refresh()
    }

    private func saveTerm(_ id: String?, english: String, chinese: String, confirmed: Bool) async {
        var arguments = ["materials", "term-save", kind, ownerID, english, "--chinese", chinese]
        if let id { arguments += ["--id", id] }
        if confirmed { arguments.append("--confirmed") }
        await workspace.perform(arguments); await reload()
    }
}
