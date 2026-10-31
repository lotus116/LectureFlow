import AppKit
import LectureFlowCore
import SwiftUI
import UniformTypeIdentifiers

struct MaterialList: Decodable {
    var documents: [CourseDocument] = []
    var terms: [GlossaryTerm] = []
    var jobs: [KnowledgeJob] = []
    var scope: String?
}
struct OutlineEntry: Decodable, Identifiable {
    let page: Int
    let title: String
    var id: Int { page }
}
struct CourseDocument: Decodable, Identifiable {
    let id: String
    let kind: String
    let original_name: String
    let page_count: Int
    let path: String
    let preview: String
    let outline: [OutlineEntry]
}
struct TermSource: Decodable { let document_id: String; let page: Int }
struct GlossaryTerm: Decodable, Identifiable {
    let id: String
    let kind: String
    let owner_id: String
    let english: String
    let chinese: String
    let confirmed: Int
    let sources: String
    let definition: String
    let score: Int
    let origin: String
    let excluded: Int
    var references: [TermSource] { (try? JSONDecoder().decode([TermSource].self, from: Data(sources.utf8))) ?? [] }
    var status: String {
        if excluded == 1 { return "已停用" }
        if chinese.isEmpty { return "待补全中文" }
        if origin == "manual" { return "人工" }
        return "已启用"
    }
}
struct KnowledgeJob: Decodable, Identifiable, Sendable {
    let id: String
    let kind: String
    let owner_id: String
    let state: String
    let next_unit: Int
    let total_units: Int
    let error: String?
    var label: String {
        switch state {
        case "running": return "正在本机补全中文"
        case "queued": return "等待本地中文补全"
        case "paused": return "中文补全已暂停"
        case "completed": return "中文补全完成"
        case "cancelled": return "中文补全已取消"
        default: return "中文补全未完成"
        }
    }
}

struct MaterialsView: View {
    @ObservedObject var workspace: Workspace
    let courseID: String
    var showTermsInitially = false
    @State private var materials = MaterialList()
    @State private var importing = false
    @State private var working = false
    @State private var progress = ""
    @State private var search = ""
    @State private var section = "overview"
    @State private var documentID: String?
    @State private var selectedTerms: Set<String> = []
    @State private var editID: String?
    @State private var english = ""
    @State private var chinese = ""
    @State private var editing = false
    @State private var removing: CourseDocument?
    @State private var previewing: CourseDocument?
    @State private var forceOCR = false
    @State private var reloadID = UUID()
    @State private var error: String?
    private var scopedDocuments: [CourseDocument] { materials.documents.filter { documentID == nil || $0.id == documentID } }
    private var visibleTerms: [GlossaryTerm] {
        materials.terms.filter { term in
            (documentID == nil || term.references.contains { $0.document_id == documentID }) &&
            (search.isEmpty || term.english.localizedCaseInsensitiveContains(search) || term.chinese.contains(search))
        }
    }
    private var job: KnowledgeJob? { materials.jobs.first { !["completed", "cancelled"].contains($0.state) } ?? materials.jobs.first }
    private var activeTerms: Int { materials.terms.filter { $0.excluded == 0 }.count }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("\(materials.documents.count) 份课程资料 · \(materials.documents.reduce(0) { $0 + $1.page_count }) 页 · \(activeTerms) 个辅助术语")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Menu {
                    Toggle("PDF 强制 OCR", isOn: $forceOCR)
                    Button("补全全部缺失中文") {
                        Task { await batch("translate", ids: materials.terms.filter { $0.chinese.isEmpty && $0.excluded == 0 }.map(\.id)) }
                    }
                } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 24)
                Button("导入课件", systemImage: "plus") { importing = true }.disabled(working)
            }
            Text("本课程共享 · 自动术语，相关时辅助识别与翻译")
                .font(.system(size: LFChrome.captionSize))
                .foregroundStyle(.secondary)
            if working { HStack { ProgressView().controlSize(.mini); Text(progress) }.font(.caption) }
            HSplitView {
                VStack(spacing: 0) {
                    List(selection: $documentID) {
                        Label("全部资料", systemImage: "books.vertical").tag(String?.none)
                        ForEach(materials.documents) { document in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(document.original_name).lineLimit(2)
                                Text("\(document.page_count) 页").font(.caption2).foregroundStyle(.secondary)
                            }.padding(.vertical, 4).tag(Optional(document.id))
                                .contextMenu {
                                    Button("查看文字") { previewing = document }
                                    Button("打开课件") { NSWorkspace.shared.open(URL(fileURLWithPath: document.path)) }
                                    Divider()
                                    Button("移除课件", role: .destructive) { removing = document }
                                }
                        }
                    }.listStyle(.sidebar)
                    Text("PDF · PPTX · PPT\n可批量导入或拖入文件")
                        .font(.caption2).foregroundStyle(.tertiary).padding(10)
                }.frame(minWidth: 140, idealWidth: 175, maxWidth: 210)
                VStack(alignment: .leading, spacing: 10) {
                    Picker("查看", selection: $section) {
                        Text("文件与大纲").tag("overview"); Text("查看辅助术语").tag("terms")
                    }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 320)
                    if section == "terms" { termsView }
                    else { overviewView }
                }.padding(.leading, 10).frame(minWidth: 350, maxWidth: .infinity, maxHeight: .infinity)
            }.background(.background, in: RoundedRectangle(cornerRadius: 8))
            if let job { jobView(job) }
        }
        .onAppear { if showTermsInitially { section = "terms" } }
        .task(id: courseID) {
            await reload()
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                if materials.jobs.contains(where: { ["queued", "running"].contains($0.state) }) { await reload() }
            }
        }
        .onChange(of: documentID) { _, _ in selectedTerms = [] }
        .onChange(of: search) { _, _ in selectedTerms.formIntersection(Set(visibleTerms.map(\.id))) }
        .fileImporter(isPresented: $importing, allowedContentTypes: [
            .pdf, UTType(filenameExtension: "pptx") ?? .data, UTType(filenameExtension: "ppt") ?? .data
        ], allowsMultipleSelection: true) { result in
            switch result {
            case .success(let urls): Task { await importFiles(urls) }
            case .failure(let error): self.error = error.localizedDescription
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            let supported = urls.filter { ["pdf", "ppt", "pptx"].contains($0.pathExtension.lowercased()) }
            guard !supported.isEmpty, !working else { return false }
            Task { await importFiles(supported) }; return true
        }
        .sheet(isPresented: $editing) { termEditor }
        .sheet(item: $previewing) { document in
            VStack(alignment: .leading, spacing: 14) {
                HStack { Text(document.original_name).font(.headline); Spacer(); Button("完成") { previewing = nil } }
                ScrollView { Text(document.preview).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled) }
            }.padding(22).frame(width: 720, height: 540)
        }
        .confirmationDialog("移除“\(removing?.original_name ?? "")”？只删除受管副本；仅由该文件支持的自动术语会停用。", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button("移除课件", role: .destructive) {
                if let item = removing { Task { await remove(item) } }; removing = nil
            }
        }
        .alert("操作未完成", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("知道了") { error = nil }
        } message: { Text(error ?? "") }
    }

    private var overviewView: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 14) {
                if scopedDocuments.isEmpty {
                    EmptyPanel(
                        title: "可选：导入课程资料",
                        systemImage: "doc.text.magnifyingglass",
                        description: "不导入也能录音与转写。导入后自动维护轻量术语。"
                    )
                    .frame(minHeight: 240)
                }
                ForEach(scopedDocuments) { document in
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text(document.original_name).font(.headline).lineLimit(1)
                            Spacer()
                            Button("原文件") { NSWorkspace.shared.open(URL(fileURLWithPath: document.path)) }
                            Button("查看文字") { previewing = document }
                        }
                        ForEach(document.outline.prefix(80)) { entry in
                            HStack(alignment: .top, spacing: 8) {
                                Text("\(entry.page)").font(.caption.monospaced()).foregroundStyle(.secondary).frame(width: 28, alignment: .trailing)
                                Text(entry.title.isEmpty ? "本页无可提取文字" : entry.title).lineLimit(2)
                            }
                        }
                        if document.outline.count > 80 { Text("其余页面可在提取文字中查看").font(.caption).foregroundStyle(.secondary) }
                    }
                    .padding(.vertical, LFChrome.space12)
                    Divider().opacity(0.5)
                }
            }.padding(.vertical, LFChrome.space4)
        }
    }

    private var termsView: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                TextField("搜索英文或中文", text: $search).textFieldStyle(.roundedBorder)
                Button("添加") { editID = nil; english = ""; chinese = ""; editing = true }
                Button("编辑") { if let term = materials.terms.first(where: { selectedTerms.contains($0.id) }) { edit(term) } }
                    .disabled(selectedTerms.count != 1)
            }
            HStack(spacing: 8) {
                Button(selectedTerms.count == visibleTerms.count && !visibleTerms.isEmpty ? "取消全选" : "全选") {
                    selectedTerms = selectedTerms.count == visibleTerms.count ? [] : Set(visibleTerms.map(\.id))
                }
                Text("\(selectedTerms.count) 项").font(.caption).foregroundStyle(.secondary)
                Spacer(minLength: 0)
                Button("补全中文") { Task { await batch("translate") } }.disabled(selectedTerms.isEmpty || working)
                Menu {
                    Button("停用") { Task { await batch("exclude") } }
                    Button("恢复启用") { Task { await batch("include") } }
                } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 22)
                    .disabled(selectedTerms.isEmpty || working)
            }.controlSize(.small)
            Table(visibleTerms, selection: $selectedTerms) {
                TableColumn("英文") { term in
                    Text(term.english).help(term.definition).onTapGesture(count: 2) { edit(term) }
                }.width(min: 110, ideal: 160)
                TableColumn("中文") { term in
                    Text(term.chinese.isEmpty ? "可留空" : term.chinese).foregroundStyle(term.chinese.isEmpty ? .tertiary : .primary)
                        .onTapGesture(count: 2) { edit(term) }
                }.width(min: 90, ideal: 150)
                TableColumn("状态") { term in
                    Text(term.status).font(.caption).foregroundStyle(term.excluded == 1 ? Color.secondary : Color.teal)
                }.width(90)
                TableColumn("出处") { term in Text(source(term)).font(.caption).foregroundStyle(.secondary).help(source(term)) }.width(min: 70, ideal: 120)
            }
            Text("术语默认自动启用。双击编辑；人工修改不会被重新解析覆盖。停用后不再参与识别与翻译。")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    private var termEditor: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("编辑辅助术语").font(.title2.bold())
            TextField("英文术语或缩写", text: $english).textFieldStyle(.roundedBorder)
            TextField("中文对照（可留空）", text: $chinese).textFieldStyle(.roundedBorder)
            if let term = materials.terms.first(where: { $0.id == editID }) {
                Text(source(term)).font(.caption).foregroundStyle(.secondary)
                if !term.definition.isEmpty { Text(term.definition).font(.callout).foregroundStyle(.secondary).textSelection(.enabled) }
            }
            HStack { Spacer(); Button("取消") { editing = false }
                Button("保存") {
                    let key = editID, en = english, zh = chinese
                    editing = false
                    Task { await saveTerm(key, english: en, chinese: zh) }
                }.keyboardShortcut(.defaultAction).disabled(english.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }.padding(24).frame(width: 460)
    }
    private func edit(_ term: GlossaryTerm) { editID = term.id; english = term.english; chinese = term.chinese; editing = true }
    private func source(_ term: GlossaryTerm) -> String {
        term.references.prefix(3).map { reference in
            let name = materials.documents.first { $0.id == reference.document_id }?.original_name ?? "历史课件"
            return "\(name) · p.\(reference.page)"
        }.joined(separator: " / ")
    }
    private func jobView(_ job: KnowledgeJob) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Image(systemName: "character.book.closed")
                Text(job.state == "queued" && !workspace.hasModel("translate") ? "等待下载本地翻译模型" : job.label)
                Text("\(job.next_unit)/\(job.total_units)").monospacedDigit().foregroundStyle(.secondary)
                Spacer()
                if ["queued", "running"].contains(job.state) { Button("暂停") { Task { await control(job, "pause") } } }
                if ["paused", "failed"].contains(job.state) { Button(job.state == "paused" ? "继续" : "重试") { Task { await control(job, job.state == "paused" ? "resume" : "retry") } } }
                if ["queued", "running", "paused", "failed"].contains(job.state) { Button("取消") { Task { await control(job, "cancel") } } }
            }.font(.caption)
            if job.state == "running" { ProgressView(value: Double(job.next_unit) / Double(max(1, job.total_units))).controlSize(.mini) }
            if let error = job.error { Text(error).font(.caption2).foregroundStyle(.red).lineLimit(2) }
        }
    }
    private func reload() async {
        let request = UUID(); reloadID = request
        do {
            let loaded = try JSONDecoder().decode(MaterialList.self, from: await workspace.backend.call(["materials", "list", "course", courseID]))
            guard !Task.isCancelled, reloadID == request else { return }
            materials = loaded
            selectedTerms.formIntersection(Set(visibleTerms.map(\.id)))
            if let id = documentID, !loaded.documents.contains(where: { $0.id == id }) { documentID = nil }
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    private func importFiles(_ urls: [URL]) async {
        guard !working else { return }
        working = true; defer { working = false }
        for (index, url) in urls.enumerated() {
            let accessed = url.startAccessingSecurityScopedResource()
            defer { if accessed { url.stopAccessingSecurityScopedResource() } }
            progress = "正在导入 \(index + 1)/\(urls.count)：\(url.lastPathComponent)"
            do {
                var arguments = ["materials", "import", "course", courseID, url.path]
                let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("lectureflow-pages-\(UUID().uuidString).json")
                defer { try? FileManager.default.removeItem(at: temporary) }
                if url.pathExtension.lowercased() == "pdf" {
                    let ocr = forceOCR
                    let extraction = try await Task.detached(priority: .userInitiated) { try DocumentExtractor.pdf(url, forceOCR: ocr) }.value
                    try JSONEncoder().encode(extraction).write(to: temporary, options: .atomic)
                    arguments += ["--pages-file", temporary.path]
                }
                _ = try await workspace.backend.call(arguments)
            } catch { self.error = "\(url.lastPathComponent)：\(error.localizedDescription)"; break }
        }
        await reload(); await workspace.refresh()
    }
    private func batch(_ operation: String, ids: [String]? = nil) async {
        let keys = ids ?? Array(selectedTerms)
        guard !keys.isEmpty, !working else { return }
        working = true; progress = "正在更新术语…"; defer { working = false }
        do {
            let payload: [String: Any] = ["kind": "course", "owner_id": courseID, "term_ids": keys, "operation": operation]
            _ = try await workspace.backend.call(["materials", "terms-batch"], input: JSONSerialization.data(withJSONObject: payload))
            await reload(); await workspace.refresh()
        } catch { self.error = error.localizedDescription }
    }
    private func control(_ job: KnowledgeJob, _ action: String) async {
        do { _ = try await workspace.backend.call(["materials", "job-control", job.id, action]); await reload(); await workspace.refresh() }
        catch { self.error = error.localizedDescription }
    }
    private func remove(_ item: CourseDocument) async {
        do { _ = try await workspace.backend.call(["materials", "remove", item.id, "--confirmed"]); await reload(); await workspace.refresh() }
        catch { self.error = error.localizedDescription }
    }
    private func saveTerm(_ id: String?, english: String, chinese: String) async {
        var arguments = ["materials", "term-save", "course", courseID, english, "--chinese", chinese, "--confirmed"]
        if let id { arguments += ["--id", id] }
        do { _ = try await workspace.backend.call(arguments); await reload(); await workspace.refresh() }
        catch { self.error = error.localizedDescription }
    }
}
