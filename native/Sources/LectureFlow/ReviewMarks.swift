import Foundation
import SwiftUI

struct ReviewMark: Decodable, Identifiable, Sendable {
    let id: String
    let lecture_id: String
    let capture_id: String
    let capture_time: Double
    let caption_id: String?
    let reviewed: Int
    let removed: Int?
    let created_at: String
}
struct ReviewMarkList: Decodable { let marks: [ReviewMark]; let active_count: Int? }
struct ReviewText: Decodable, Identifiable {
    let id: String
    let start: Double
    let end: Double
    let english: String
    let chinese: String
}
struct ReviewAudio: Decodable {
    let segment_id: String
    let files: [String]
    let offset: Double
    let position: Double
    let pre_roll_seconds: Double
}
struct ReviewDetail: Decodable {
    let mark: ReviewMark
    let captions: [ReviewText]
    let target_caption_id: String?
    let audio: ReviewAudio?
    let audio_message: String
}

@MainActor
final class ReviewMarks: ObservableObject {
    @Published private(set) var marks: [ReviewMark] = []
    @Published private(set) var activeCount = 0
    @Published private(set) var detail: ReviewDetail?
    @Published private(set) var saving = false
    @Published var error: String?
    @Published private(set) var feedback: String?
    @Published var showingRemoved = false {
        didSet { if showingRemoved != oldValue { marks = []; selectedID = nil; Task { await refresh() } } }
    }
    @Published var selectedID: String? { didSet { if selectedID != oldValue { detail = nil; loadDetail() } } }
    private let call: ([String]) async throws -> Data
    private var lectureID: String?
    private var loadToken = UUID()
    private var detailToken = UUID()
    private var writes: [UUID: Task<Void, Never>] = [:]
    private var failed: [UUID: [String]] = [:]
    var hasUnsavedChanges: Bool { !failed.isEmpty || !writes.isEmpty }

    init(backend: Backend) { call = { try await backend.call($0) } }
    init(call: @escaping ([String]) async throws -> Data) { self.call = call }

    func selectLecture(_ id: String?) {
        lectureID = id; marks = []; activeCount = 0; detail = nil; selectedID = nil; showingRemoved = false
        loadToken = UUID(); detailToken = UUID(); feedback = nil
        Task { await refresh() }
    }

    func refresh() async {
        guard let id = lectureID else { return }
        let token = UUID(); loadToken = token
        let removed = showingRemoved
        do {
            let result = try JSONDecoder().decode(ReviewMarkList.self,
                from: await call(["review", "list", id] + (removed ? ["--removed"] : [])))
            guard lectureID == id, loadToken == token, showingRemoved == removed else { return }
            marks = result.marks
            activeCount = result.active_count ?? (removed ? activeCount : result.marks.count)
            if let key = selectedID, !marks.contains(where: { $0.id == key }) { selectedID = nil }
            if selectedID == nil { selectedID = marks.first(where: { $0.reviewed == 0 })?.id ?? marks.first?.id }
        } catch { if lectureID == id, loadToken == token { self.error = error.localizedDescription } }
    }

    func loadDetail() {
        guard let lecture = lectureID, let id = selectedID else { return }
        let token = UUID(); detailToken = token
        Task {
            do {
                let result = try JSONDecoder().decode(ReviewDetail.self,
                    from: await call(["review", "detail", lecture, id]))
                guard lectureID == lecture, selectedID == id, detailToken == token else { return }
                detail = result
            } catch { if lectureID == lecture, detailToken == token { self.error = error.localizedDescription } }
        }
    }

    func add(lecture: String, capture: String, time: Double, caption: String? = nil) {
        let id = UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "")
        var arguments = ["review", "add", lecture, capture, String(time), "--id", id]
        if let caption { arguments += ["--caption-id", caption] }
        write(arguments, success: "已标记 \(reviewClock(time))")
    }
    func setReviewed(_ mark: ReviewMark, value: Bool) {
        write(["review", "done", mark.lecture_id, mark.id, "--value", value ? "1" : "0"], success: "已更新复习状态")
    }
    func remove(_ mark: ReviewMark) {
        write(["review", "remove", mark.lecture_id, mark.id, "--confirmed"], success: "已移除标记")
    }
    func restore(_ mark: ReviewMark) {
        write(["review", "restore", mark.lecture_id, mark.id], success: "已恢复标记")
    }
    func retryFailed() {
        let pending = failed; failed.removeAll()
        for (id, arguments) in pending { write(arguments, success: "标记已保存", request: id) }
    }
    private func write(_ arguments: [String], success: String, request: UUID = UUID()) {
        saving = true; error = nil; feedback = "正在保存标记…"
        writes[request] = Task {
            do {
                _ = try await call(arguments)
                failed.removeValue(forKey: request)
                if lectureID == arguments[2] { feedback = success; await refresh(); loadDetail() }
            } catch {
                failed[request] = arguments
                self.error = "复习标记尚未保存，可点击重试。" + error.localizedDescription
                feedback = nil
            }
            writes.removeValue(forKey: request)
            saving = !writes.isEmpty
        }
    }
    func finishWrites() async {
        for task in Array(writes.values) { await task.value }
    }
}

func reviewClock(_ seconds: Double) -> String {
    guard seconds.isFinite, seconds >= 0, seconds < Double(Int.max) else { return "时间无效" }
    let value = max(0, Int(seconds))
    return value >= 3600 ? String(format: "%d:%02d:%02d", value / 3600, value / 60 % 60, value % 60)
        : String(format: "%02d:%02d", value / 60, value % 60)
}

private func reviewDate(_ value: String) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let fractional = formatter.date(from: value)
    formatter.formatOptions = [.withInternetDateTime]
    return (fractional ?? formatter.date(from: value))?
        .formatted(date: .abbreviated, time: .shortened) ?? value
}

struct ReviewCurrentButton: View {
    @ObservedObject var workspace: Workspace
    @ObservedObject var live: LiveController
    var compact = false
    @State private var acknowledged = false
    var body: some View {
        Button { workspace.markCurrentForReview() } label: {
            if compact {
                Text(acknowledged ? "✓" : "🎯").frame(minWidth: 26, minHeight: 24)
            }
            else { Text("🎯 标记此处") }
        }
        .disabled(!live.isCapturing || workspace.reviews.saving || workspace.isExiting)
        .help("记录当前听到的位置，课后回听复习")
        .accessibilityLabel("标记当前复习位置")
        .accessibilityIdentifier("review-mark-current")
        .task(id: workspace.reviews.feedback) {
            acknowledged = workspace.reviews.feedback?.hasPrefix("已标记") == true
            if acknowledged {
                try? await Task.sleep(for: .seconds(2))
                if !Task.isCancelled { acknowledged = false }
            }
        }
    }
}

struct ReviewMarksView: View {
    @ObservedObject var workspace: Workspace
    @ObservedObject var store: ReviewMarks
    @ObservedObject var playback: AudioPlayback
    @State private var removing: ReviewMark?
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("🎯 复习标记").font(.title2.bold())
                Spacer()
                Toggle("已移除", isOn: $store.showingRemoved).toggleStyle(.checkbox)
                Button("刷新") { Task { await store.refresh(); store.loadDetail() } }
                Button("完成") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Text(workspace.lecture?.title ?? "Lecture").foregroundStyle(.secondary)
            if let error = store.error {
                HStack {
                    Text(error).foregroundStyle(.red).textSelection(.enabled)
                    if store.hasUnsavedChanges { Button("重试保存") { store.retryFailed() }.disabled(store.saving) }
                }.font(.callout)
            }
            if store.marks.isEmpty {
                ContentUnavailableView(store.showingRemoved ? "没有已移除的标记" : "还没有复习标记",
                    systemImage: "scope", description: Text(store.showingRemoved
                        ? "移除的标记会保留在这里，可以恢复。" : "听不懂时点 🎯；下课后在这里找到对应文字和录音。"))
            } else {
                HSplitView {
                    List(store.marks, selection: $store.selectedID) { mark in
                        HStack {
                            Image(systemName: mark.reviewed == 1 ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(mark.reviewed == 1 ? .green : .secondary)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(reviewClock(mark.capture_time)).monospacedDigit().fontWeight(.medium)
                                Text(reviewDate(mark.created_at))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                        }
                        .accessibilityLabel("\(reviewClock(mark.capture_time))，\(mark.reviewed == 1 ? "已复习" : "待复习")")
                        .tag(mark.id)
                    }.frame(minWidth: 155, idealWidth: 180, maxWidth: 220)
                    detailView.frame(minWidth: 300, maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .padding(20).frame(minWidth: 620, idealWidth: 760, minHeight: 400, idealHeight: 540)
        .task { await store.refresh(); store.loadDetail() }
        .onDisappear { playback.stop() }
        .onChange(of: store.selectedID) { _, _ in playback.stop() }
        .confirmationDialog("移除这个复习标记？", isPresented: Binding(
            get: { removing != nil }, set: { if !$0 { removing = nil } })) {
                if let mark = removing {
                    Button("移除标记", role: .destructive) { store.remove(mark); removing = nil }
                }
            } message: { Text("录音和字幕会保留；标记可从“已移除”中恢复。") }
    }

    @ViewBuilder private var detailView: some View {
        if let detail = store.detail {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("标记位置 \(reviewClock(detail.mark.capture_time))").font(.headline)
                    Spacer()
                    if detail.mark.removed != 1 {
                        Button(detail.mark.reviewed == 1 ? "设为待复习" : "标为已复习") {
                            store.setReviewed(detail.mark, value: detail.mark.reviewed == 0)
                        }.disabled(store.saving)
                    }
                }
                if let audio = detail.audio {
                    HStack {
                        Button(playback.reviewID == detail.mark.id ? "停止回听" : "回听原音") {
                            playback.playReview(audio, markID: detail.mark.id)
                        }.disabled(workspace.live.isCapturing)
                        Text(workspace.live.isCapturing ? "结束采集后可回听" : "从标记前\(Int(audio.pre_roll_seconds))秒开始")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                } else {
                    Text(detail.audio_message).font(.callout).foregroundStyle(.secondary)
                }
                if let error = playback.error { Text(error).foregroundStyle(.red) }
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if detail.captions.isEmpty {
                            Text("此位置暂无已保存字幕；保存了原音时仍可回听。")
                                .foregroundStyle(.secondary)
                        }
                        ForEach(detail.captions) { caption in
                            VStack(alignment: .leading, spacing: 6) {
                                Text(caption.id == detail.target_caption_id ? "标记附近" : "相邻内容")
                                    .font(.caption).foregroundStyle(.secondary)
                                if !caption.english.isEmpty { Text(caption.english) }
                                if !caption.chinese.isEmpty { Text(caption.chinese).foregroundStyle(.secondary) }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                                .padding(10)
                                .background(caption.id == detail.target_caption_id ? Color.accentColor.opacity(0.08) : .clear)
                        }
                    }.textSelection(.enabled)
                }
                Spacer(minLength: 0)
                if detail.mark.removed == 1 {
                    Button("恢复标记") { store.restore(detail.mark) }.disabled(store.saving)
                } else {
                    Button("移除标记", role: .destructive) { removing = detail.mark }.disabled(store.saving)
                }
            }.padding(.leading, 14)
        } else {
            Text(store.selectedID == nil ? "选择一个标记开始复习" : "正在查找对应内容…")
                .foregroundStyle(.secondary)
        }
    }
}
