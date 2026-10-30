import SwiftUI

struct BackgroundItem: Identifiable {
    let key: String
    let kind: String
    let owner: String
    let title: String
    let state: String
    let done: Int
    let total: Int
    var id: String { kind + ":" + key }
    var active: Bool { ["queued", "running"].contains(state) }
    var label: String {
        switch state {
        case "queued": return "等待处理"
        case "running": return "正在处理"
        case "paused": return "已暂停"
        default: return "需要重试"
        }
    }
    func arguments(_ action: String) -> [String] {
        kind == "audio" ? ["library", "task", key, action] : ["materials", "job-control", key, action]
    }
}

extension Workspace {
    var backgroundItems: [BackgroundItem] {
        let states = ["queued", "running", "paused", "failed"]
        var result = overview.courses.flatMap { course in
            course.lectures.flatMap { lecture in
                lecture.tasks.filter { states.contains($0.state) }.map { task in
                    BackgroundItem(key: task.id, kind: "audio", owner: course.name + " · " + lecture.title,
                        title: "录音转写", state: task.state, done: task.next_unit, total: task.total_units)
                }
            }
        }
        result += (overview.knowledge_jobs ?? []).filter { states.contains($0.state) }.map { job in
            let course = overview.courses.first { $0.id == job.owner_id }
            let lecture = overview.courses.flatMap(\.lectures).first { $0.id == job.owner_id }
            return BackgroundItem(key: job.id, kind: "terms", owner: course?.name ?? lecture?.title ?? "课件词库",
                title: "术语中文补全", state: job.state, done: job.next_unit, total: job.total_units)
        }
        return result.sorted { ($0.active ? 0 : 1, $0.id) < ($1.active ? 0 : 1, $1.id) }
    }
}

struct BackgroundQueueView: View {
    @ObservedObject var workspace: Workspace
    @Environment(\.dismiss) private var dismiss
    @State private var working = false
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("后台任务").font(.title2.bold())
                Spacer()
                Button("全部暂停") { Task { await pauseAll() } }
                    .disabled(working || !workspace.backgroundItems.contains { $0.active })
                Button("完成") { dismiss() }
            }
            Text("包含所有课程的后台任务。当前区间完成后暂停，已处理内容保留；不会停止录音。实时字幕优先时，冲突任务会安全让路并显示等待。")
                .font(.caption).foregroundStyle(.secondary)
            List(workspace.backgroundItems) { item in
                HStack(spacing: 12) {
                    Image(systemName: item.kind == "audio" ? "waveform" : "character.book.closed")
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.title + " · " + item.owner).lineLimit(1)
                        Text("\(item.label) · \(item.done)/\(item.total)").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(item.active ? "暂停" : item.state == "failed" ? "重试" : "继续") {
                        Task { await control(item, item.active ? "pause" : item.state == "failed" ? "retry" : "resume") }
                    }.disabled(working)
                }.padding(.vertical, 5)
            }.overlay {
                if workspace.backgroundItems.isEmpty {
                    EmptyPanel(title: "没有待处理任务", systemImage: "checkmark.circle", description: "后台转写和词库补全会出现在这里。")
                }
            }
            if working { ProgressView().controlSize(.small) }
        }.padding(22).frame(width: 680, height: 440)
            .alert("操作未完成", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
                Button("知道了") { error = nil }
            } message: { Text(error ?? "") }
    }
    private func control(_ item: BackgroundItem, _ action: String) async {
        working = true; defer { working = false }
        do { _ = try await workspace.backend.call(item.arguments(action)) }
        catch { self.error = error.localizedDescription }
        await workspace.refresh()
    }
    private func pauseAll() async {
        working = true; defer { working = false }
        let pending = workspace.backgroundItems.filter(\.active)
        for item in pending {
            do { _ = try await workspace.backend.call(item.arguments("pause")) }
            catch {
                await workspace.refresh()
                if workspace.backgroundItems.contains(where: { $0.id == item.id && $0.active }) {
                    self.error = error.localizedDescription
                }
            }
        }
        await workspace.refresh()
    }
}
