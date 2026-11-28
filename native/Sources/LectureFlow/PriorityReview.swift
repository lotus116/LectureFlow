import SwiftUI

private struct PriorityItems: Decodable {
    struct Item: Decodable, Identifiable {
        let session: String, original: String, state: String
        let id: String
        let start: Double, score: Double
    }
    let items: [Item]
}

private struct PriorityCandidate: Decodable {
    let candidate: String, original: String, state: String
    let changes: [String]
    let generation: Int
    let audio: ReviewAudio
}

struct PriorityReview: View {
    @ThemePalette private var palette
    let lecture: Lecture
    let backend: Backend
    @ObservedObject var playback: AudioPlayback
    @Environment(\.dismiss) private var dismiss
    @State private var items: [PriorityItems.Item] = []
    @State private var selected: PriorityItems.Item?
    @State private var detail: PriorityCandidate?
    @State private var busy = false
    @State private var error: String?

    @FocusState private var focusedControl: String?

    private var focusOrder: [String] {
        guard let detail else { return items.map { "priority-item-" + $0.id } + ["priority-done"] }
        var controls = ["priority-play", "priority-back"]
        if detail.state == "ready" { controls += ["priority-accept", "priority-ignore"] }
        if ["accepted", "ignored"].contains(detail.state) { controls.append("priority-undo") }
        return controls + ["priority-done"]
    }

    var body: some View {
        GeometryReader { geometry in
        let compact = geometry.size.height < 400
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("优先复核").font(.system(size: LFChrome.sheetTitleSize, weight: .semibold))
                Spacer()
                Button("完成") { activate("priority-done") }.keyboardShortcut(.cancelAction)
                    .reviewFocus("priority-done", $focusedControl, activate: activate)
            }.padding(compact ? 12 : 16)
            Divider()
            if compact && detail != nil {
                Label("上下滚动查看完整原稿、候选与差异", systemImage: "arrow.up.arrow.down")
                    .font(.caption).foregroundStyle(palette.secondaryText)
                    .padding(.horizontal, 12).padding(.vertical, 4)
            }
            if let error { LFStatusBanner(error, tone: .failure).textSelection(.enabled) }
            if busy { ProgressView("正在读取本机资料…").controlSize(.small) }
            if items.isEmpty && !busy && error == nil && detail == nil {
                EmptyPanel(title: "没有可优先复核的片段", systemImage: "text.badge.checkmark",
                           description: "需要有撤回词记录的英文字幕，以及能准确定位的已保存原音。")
            } else {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let detail {
                        if geometry.size.width >= 700 {
                            HStack(alignment: .top, spacing: 24) {
                                comparisonColumn("实时原稿", text: detail.original)
                                    .frame(width: (geometry.size.width - 88) / 2)
                                comparisonColumn(detail.state == "accepted" ? "已接受的复核版本" : "候选英文", text: detail.candidate)
                                    .frame(width: (geometry.size.width - 88) / 2)
                                    .padding(.leading, 24)
                                    .overlay(alignment: .leading) { Rectangle().fill(palette.hairline).frame(width: 1) }
                            }
                        } else {
                            VStack(alignment: .leading, spacing: compact ? 12 : 24) {
                                comparisonColumn("实时原稿", text: detail.original)
                                comparisonColumn(detail.state == "accepted" ? "已接受的复核版本" : "候选英文", text: detail.candidate)
                            }
                        }
                        if !detail.changes.isEmpty {
                            ViewThatFits(in: .horizontal) {
                                HStack(spacing: 8) {
                                    ForEach(Array(detail.changes.enumerated()), id: \.offset) { _, change in
                                        LFReviewChange(change: change).fixedSize(horizontal: true, vertical: false)
                                    }
                                }
                                VStack(alignment: .leading, spacing: 6) {
                                    ForEach(Array(detail.changes.enumerated()), id: \.offset) { _, change in
                                        LFReviewChange(change: change)
                                    }
                                }
                            }.accessibilityElement(children: .contain).accessibilityLabel("英文改动")
                            .accessibilityIdentifier("priority-differences")
                        }

                    } else if items.isEmpty && !busy && error == nil {
                        Text("没有可优先复核的片段。需要有撤回词记录的英文字幕，以及能准确定位的已保存原音。")
                    } else {
                        ForEach(items) { item in
                            Button {
                                activate("priority-item-" + item.id)
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(String(format: "%.1f 秒 · %.2f 撤回词/秒", item.start, item.score))
                                        .foregroundStyle(palette.secondaryText)
                                    Text(item.original).lineLimit(2)
                                    Text(label(item.state)).font(.caption)
                                }.frame(maxWidth: .infinity, alignment: .leading).padding(20)
                            }.buttonStyle(.borderless)
                            .reviewFocus("priority-item-\(item.id)", $focusedControl, activate: activate)
                            Divider()
                        }
                    }
                }.frame(maxWidth: .infinity, alignment: .leading).padding(compact ? 12 : 20).textSelection(.enabled)
            }.scrollIndicators(.visible).disabled(busy)
            }
            if let detail, let selected {
                Divider()
                reviewActions(detail, selected: selected)
                    .padding(compact ? 10 : 16).frame(maxWidth: .infinity, alignment: .leading).background(palette.panel)
                    .disabled(busy)
            }
        }
        }
        .frame(minWidth: 410, idealWidth: 820, minHeight: 320, idealHeight: 460)
        .background(palette.canvas).foregroundStyle(palette.bodyText)
        .tint(palette.accent)
        .task { await refresh(); focusedControl = focusOrder.first }
        .onKeyPress(keys: [.tab], phases: .down) { event in
            guard !busy, event.modifiers.subtracting(.shift).isEmpty else { return .ignored }
            let order = focusOrder
            let index = focusedControl.flatMap(order.firstIndex) ?? -1
            focusedControl = order[(index + (event.modifiers.contains(.shift) ? -1 : 1) + order.count) % order.count]
            return .handled
        }
        .onDisappear { playback.stop() }
    }

    private func comparisonColumn(_ title: String, text: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 13, weight: .semibold)).foregroundStyle(palette.secondaryText)
            Text(text).font(.system(size: LFChrome.bodySize)).lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func reviewActions(_ detail: PriorityCandidate, selected: PriorityItems.Item) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Button(playback.reviewID == selected.id ? "停止回听" : "回听这段原音") {
                    activate("priority-play")
                }.buttonStyle(.bordered).reviewFocus("priority-play", $focusedControl, activate: activate)
                Spacer()
                Button("返回片段") {
                    activate("priority-back")
                }.buttonStyle(.borderless).reviewFocus("priority-back", $focusedControl, activate: activate)
            }
            if let message = playback.error {
                LFStatusBanner(message, tone: .failure)
            }
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                if detail.state == "ready" {
                    Button("接受为复核版本") { activate("priority-accept") }
                        .buttonStyle(.borderedProminent).reviewFocus("priority-accept", $focusedControl, activate: activate)
                    Button("忽略") { activate("priority-ignore") }
                        .buttonStyle(.bordered).reviewFocus("priority-ignore", $focusedControl, activate: activate)
                } else if detail.state == "same" {
                    Label("候选与原稿相同", systemImage: "equal.circle")
                        .foregroundStyle(palette.secondaryText)
                } else {
                    Button(detail.state == "accepted" ? "撤销接受" : "撤销忽略") {
                        activate("priority-undo")
                    }.reviewFocus("priority-undo", $focusedControl, activate: activate)
                }
            }
        }
        .font(.system(size: LFChrome.bodySize, weight: .medium))
        .controlSize(.regular)
    }

    private func activate(_ id: String) {
        guard !busy else { return }
        if id == "priority-done" { dismiss(); return }
        if let item = items.first(where: { id == "priority-item-" + $0.id }) {
            Task { await act(item, "prepare") }
            return
        }
        guard let detail, let selected, focusOrder.contains(id) else { return }
        switch id {
        case "priority-play": playback.playReview(detail.audio, markID: selected.id)
        case "priority-back":
            playback.stop()
            self.detail = nil
            Task { await refresh(); focusedControl = "priority-item-" + selected.id }
        case "priority-accept", "priority-ignore", "priority-undo":
            Task { await act(selected, String(id.dropFirst("priority-".count))) }
        default: break
        }
    }

    private func label(_ state: String) -> String {
        switch state {
        case "accepted": "查看已接受版本"
        case "ignored": "查看已忽略候选"
        case "ready", "same": "比较已有候选"
        default: "生成这段候选"
        }
    }

    private func refresh() async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            let data = try await backend.call(["priority-review", "list", lecture.id])
            items = try JSONDecoder().decode(PriorityItems.self, from: data).items
        } catch { self.error = error.localizedDescription }
    }

    private func act(_ item: PriorityItems.Item, _ action: String) async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            let data = try await backend.call(["priority-review", action, lecture.id, "--session",
                item.session, "--caption", item.id, "--generation", String(detail?.generation ?? 0)])
            detail = try JSONDecoder().decode(PriorityCandidate.self, from: data)
            selected = item
            let next = action == "prepare" ? "priority-play" : (action == "undo" ? "priority-accept" : "priority-undo")
            Task { @MainActor in await Task.yield(); focusedControl = next }
        } catch { self.error = error.localizedDescription }
    }
}

private extension View {
    func reviewFocus(_ id: String, _ focus: FocusState<String?>.Binding,
                     activate: @escaping (String) -> Void) -> some View {
        focusable(true, interactions: .edit).focused(focus, equals: id).accessibilityIdentifier(id)
            .onKeyPress(keys: [.space, .return], phases: .down) { event in
                guard event.modifiers.isEmpty, focus.wrappedValue == id else { return .ignored }
                activate(id)
                return .handled
            }
    }
}
