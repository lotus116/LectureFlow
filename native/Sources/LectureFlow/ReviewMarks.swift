import Foundation
import LectureFlowCore
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

    /// Match by stable source identity/time, including fragments merged for display.
    func caption(in captions: [LiveCaption]) -> LiveCaption? {
        let sameSession = captions.filter { $0.session_id == capture_id }
        if let exact = sameSession.first(where: { $0.id == caption_id }) { return exact }
        if let containing = sameSession.first(where: { $0.start <= capture_time && capture_time < $0.end }) {
            return containing
        }
        func distance(_ caption: LiveCaption) -> Double {
            min(abs(caption.start - capture_time), abs(caption.end - capture_time))
        }
        return sameSession.min(by: { distance($0) < distance($1) }).flatMap {
            distance($0) <= 3 ? $0 : nil
        }
    }
}
struct ReviewMarkList: Decodable {
    let marks: [ReviewMark]
    let active_count: Int?
    let active_marks: [ReviewMark]?
}
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
    @Published private(set) var activeMarks: [ReviewMark] = []
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
        lectureID = id; marks = []; activeMarks = []; activeCount = 0
        detail = nil; selectedID = nil; showingRemoved = false
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
            activeMarks = result.active_marks ?? (removed ? activeMarks : result.marks)
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
    func matching(_ caption: LiveCaption, in captions: [LiveCaption]) -> [ReviewMark] {
        activeMarks.filter {
            let target = $0.caption(in: captions)
            return target?.id == caption.id && target?.session_id == caption.session_id
        }
    }
    func toggle(_ caption: LiveCaption, in captions: [LiveCaption], lecture: String) {
        guard !saving, let capture = caption.session_id else { return }
        let existing = matching(caption, in: captions)
        if existing.isEmpty {
            add(lecture: lecture, capture: capture, time: caption.start, caption: caption.id)
        } else {
            for mark in existing {
                write(["review", "remove", lecture, mark.id, "--confirmed"], success: "已取消标记，可在“已移除”中恢复")
            }
        }
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
    private var marked: Bool {
        workspace.currentReviewCaption.map { workspace.isReviewMarked($0) } ?? false
    }
    var body: some View {
        Button { workspace.markCurrentForReview() } label: {
            if compact {
                Image(systemName: marked ? "bookmark.fill" : "bookmark")
                    .foregroundStyle(marked ? Color.blue : Color.primary.opacity(0.7))
                    .frame(width: 26, height: 24)
            }
            else {
                Label("标记当前", systemImage: marked ? "bookmark.fill" : "bookmark")
                    .foregroundStyle(marked ? Color.blue : Color.primary)
            }
        }
        .disabled((workspace.currentReviewCaption == nil && !live.isCapturing)
                  || workspace.reviews.saving || workspace.isExiting)
        .help(marked ? "取消最新一段字幕的复习标记" : "标记最新一段字幕；还没有字幕时记录当前时间点")
        .accessibilityLabel("标记当前")
        .accessibilityValue(marked ? "已标记" : "未标记")
        .accessibilityIdentifier("review-mark-current")
    }
}

struct ReviewCaptionButton: View {
    @ObservedObject var workspace: Workspace
    let caption: LiveCaption
    var compact = false
    var revealed = false
    @State private var hovered = false
    @FocusState private var keyboardFocused: Bool
    private var marked: Bool { workspace.isReviewMarked(caption) }
    var body: some View {
        Button { workspace.markCaptionForReview(caption, lecture: workspace.selectedLecture ?? "") } label: {
            Image(systemName: marked ? "bookmark.fill" : "bookmark")
                .font(.system(size: compact ? 13 : 15, weight: .medium))
                .foregroundStyle(marked ? Color.blue : Color.primary.opacity(0.85))
                .opacity(marked || revealed || hovered || keyboardFocused ? 1 : 0)
                .frame(width: 28, height: 28)
                .background(hovered ? Color.primary.opacity(0.07) : .clear,
                            in: RoundedRectangle(cornerRadius: 6))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($keyboardFocused)
        .background(ReviewHoverRegion { hovered = $0 })
        .disabled(caption.session_id == nil || workspace.reviews.saving || workspace.isExiting)
        .help(marked ? "取消这段的复习标记" : "标记这段内容，课后复习")
        .accessibilityLabel("\(reviewClock(caption.start)) 的复习书签")
        .accessibilityValue(marked ? "已标记" : "未标记")
        .accessibilityIdentifier("review-caption-\(caption.id)")
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
                Label("复习标记", systemImage: "bookmark.fill").font(.title2.bold())
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
                        ? "移除的标记会保留在这里，可以恢复。" : "听不懂时点“标记当前”或段落右侧的书签；下课后在这里复习。"))
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
