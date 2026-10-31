import SwiftUI

struct TrashItem: Decodable, Identifiable {
    let id: String
    let kind: String
    let name: String
    let created_at: String?
    let archived_at: String?
    var label: String { kind == "course" ? "课程" : kind == "lecture" ? "Lecture" : "录音" }
}
struct TrashList: Decodable { let items: [TrashItem] }
private struct TrashPreview: Decodable {
    let digest: String
    let bytes: Int64
    let courses: Int
    let lectures: Int
    let segments: Int
    let documents: Int
}

struct TrashView: View {
    @ObservedObject var workspace: Workspace
    @Environment(\.dismiss) private var dismiss
    @State private var items: [TrashItem] = []
    @State private var preview: TrashPreview?
    @State private var working = false
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("废纸篓", systemImage: "trash").font(.title2.bold())
                Spacer()
                Button("清空废纸篓", role: .destructive) { Task { await prepareEmpty() } }
                    .disabled(items.isEmpty || working || workspace.live.flags.needsMicrophone)
                Button("完成") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Text("恢复会放回所属课程。清空仅影响这里的受管副本，原始导入文件始终保留。")
                .font(.callout).foregroundStyle(.secondary)
            List(items) { item in
                HStack(spacing: 12) {
                    Image(systemName: item.kind == "segment" ? "waveform" : "folder").foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(item.name)
                        Text("\(item.label) · 创建 \(date(item.created_at)) · 删除 \(date(item.archived_at))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("恢复") { Task { await restore(item) } }.disabled(working)
                }.padding(.vertical, 4)
            }.overlay {
                if items.isEmpty {
                    EmptyPanel(
                        title: "废纸篓为空",
                        systemImage: "trash",
                        description: "删除的课程、Lecture 和录音会出现在这里。"
                    )
                }
            }
            if working { ProgressView().controlSize(.small) }
        }.padding(22).frame(width: 680, height: 440).task { await reload() }
            .alert("永久清空废纸篓？", isPresented: Binding(get: { preview != nil }, set: { if !$0 { preview = nil } })) {
                Button("取消", role: .cancel) { preview = nil }
                Button("永久清空", role: .destructive) {
                    if let proposal = preview { Task { await empty(proposal) } }; preview = nil
                }
            } message: {
                if let proposal = preview {
                    Text("包括 \(proposal.courses) 门课程、\(proposal.lectures) 个 Lecture、\(proposal.segments) 段录音及 \(proposal.documents) 份课件，共 \(ByteCountFormatter.string(fromByteCount: proposal.bytes, countStyle: .file))。关联转写也将删除，无法从废纸篓恢复。")
                }
            }
            .alert("未完成", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
                Button("知道了") { error = nil }
            } message: { Text(error ?? "") }
    }
    private func date(_ value: String?) -> String {
        guard let value, !value.isEmpty else { return "较早版本" }
        return String(value.prefix(16)).replacingOccurrences(of: "T", with: " ")
    }
    private func reload() async {
        do { items = try JSONDecoder().decode(TrashList.self, from: await workspace.backend.call(["library", "trash"])).items }
        catch { self.error = error.localizedDescription }
    }
    private func prepareEmpty() async {
        working = true; defer { working = false }
        do { preview = try JSONDecoder().decode(TrashPreview.self, from: await workspace.backend.call(["library", "trash-preview"])) }
        catch { self.error = error.localizedDescription }
    }
    private func restore(_ item: TrashItem) async {
        working = true; defer { working = false }
        do { _ = try await workspace.backend.call(["library", "restore", item.kind, item.id]); await workspace.refresh(); await reload() }
        catch { self.error = error.localizedDescription }
    }
    private func empty(_ proposal: TrashPreview) async {
        working = true; defer { working = false }
        do {
            _ = try await workspace.backend.call(["library", "empty-trash", "--digest", proposal.digest, "--confirmed"])
            await workspace.refresh(); await reload()
        } catch { self.error = error.localizedDescription; await reload() }
    }
}
