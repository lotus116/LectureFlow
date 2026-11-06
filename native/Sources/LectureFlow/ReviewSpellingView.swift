import AppKit
import SwiftUI

private struct ReviewDocument: Decodable, Identifiable {
    let id: String
    let name: String
}

private struct ReviewPage: Decodable, Identifiable {
    let document: String
    let name: String
    let page: Int
    let path: String
    let text: String
    var id: String { "\(document):\(page)" }
}

private struct SpellingEvidence: Decodable {
    let old: String
    let term: String
    let line: String
    let source: ReviewPage
}

private struct ReviewHits: Decodable {
    let spelling: SpellingEvidence?
    let pages: [ReviewPage]
}

private struct ReviewQuery: Encodable, Equatable {
    let sentence: String
    let neighbor: String
    let selection: String
    let document: String
}

struct ReviewSpellingView: View {
    let backend: Backend
    let detail: ReviewDetail
    @AppStorage private var materialID: String

    init(backend: Backend, detail: ReviewDetail) {
        self.backend = backend
        self.detail = detail
        _materialID = AppStorage(wrappedValue: "", "reviewMaterial." + detail.mark.lecture_id)
    }
    @State private var documents: [ReviewDocument] = []
    @State private var ready = false
    @State private var selectedID: String?
    @State private var wordStart: Int?
    @State private var wordCount = 1
    @State private var hits: ReviewHits?
    @State private var completed: ReviewQuery?
    @State private var lookupError: String?
    @State private var materialError: String?
    @State private var retry = 0

    private var selected: ReviewText? { detail.captions.first { $0.id == selectedID } }
    private static let wordPattern = try! Regex(#"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*(?:\+\+|#)?"#)
    private var words: [String] {
        (selected?.english ?? "").matches(of: Self.wordPattern).map { String($0.0) }
    }
    private var term: String {
        guard let start = wordStart, start < words.count else { return "" }
        return words[start..<min(start + wordCount, words.count)].joined(separator: " ")
    }
    private var query: ReviewQuery? {
        guard ready, !documents.isEmpty, let selected, !selected.english.isEmpty else { return nil }
        let neighbor = detail.captions.first { $0.id != selected.id }?.english ?? ""
        return ReviewQuery(sentence: selected.english, neighbor: neighbor, selection: term, document: materialID)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if detail.captions.isEmpty {
                    Text("此位置暂无已保存字幕。").foregroundStyle(.secondary)
                }
                ForEach(detail.captions) { line in
                    Button {
                        wordStart = nil; wordCount = 1; selectedID = line.id
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(line.id == detail.target_caption_id ? "标记时的当前句" : "上一句")
                                .font(.caption).foregroundStyle(.secondary)
                            if !line.english.isEmpty { Text(line.english) }
                            if !line.chinese.isEmpty { Text(line.chinese).foregroundStyle(.secondary) }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading).padding(9)
                        .background(selectedID == line.id ? Color.accentColor.opacity(0.14) : Color.secondary.opacity(0.07),
                                    in: RoundedRectangle(cornerRadius: 7))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .contextMenu {
                        if !line.english.isEmpty { Button("复制英文") { copy(line.english) } }
                        if !line.chinese.isEmpty { Button("复制中文") { copy(line.chinese) } }
                    }
                    .accessibilityHint("查这句在课件里的拼写")
                }
                if !detail.captions.isEmpty {
                    if !documents.isEmpty {
                        Picker("本节课件", selection: $materialID) {
                            Text("全部课程资料").tag("")
                            ForEach(documents) { Text($0.name).tag($0.id) }
                        }
                    }
                    if let error = materialError {
                        Text(error).foregroundStyle(.red)
                        Button("重新读取课件") { retry += 1 }
                    } else if ready && documents.isEmpty {
                        Text("可在所属课程的资料中导入 PPT 或 PDF。").foregroundStyle(.secondary)
                    } else if let selected, selected.english.isEmpty {
                        Text("这句没有保存英文，无法查英文拼写。").foregroundStyle(.secondary)
                    } else if let query {
                        if !words.isEmpty { wordPicker }
                        if completed != query { ProgressView("正在查课件…") }
                        else if let error = lookupError {
                            Text(error).foregroundStyle(.red)
                            Button("重新查找") { retry += 1 }
                        } else if let hits { results(hits) }
                    } else if ready {
                        Text("回听后，点要核对的那一句。").foregroundStyle(.secondary)
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        .task(id: retry) {
            do {
                let data = try await backend.call(["review", "materials", detail.mark.lecture_id])
                let list = try JSONDecoder().decode([ReviewDocument].self, from: data)
                try Task.checkCancellation()
                if !materialID.isEmpty && !list.contains(where: { $0.id == materialID }) { materialID = "" }
                documents = list; materialError = nil; ready = true
            } catch { if !Task.isCancelled { materialError = error.localizedDescription } }
        }
        .task(id: QueryTask(query: query, retry: retry)) {
            guard let query else { return }
            completed = nil; hits = nil; lookupError = nil
            do {
                let data = try await backend.call(["review", "lookup", detail.mark.lecture_id],
                                                  input: JSONEncoder().encode(query))
                try Task.checkCancellation()
                hits = try JSONDecoder().decode(ReviewHits.self, from: data)
                completed = query
            } catch {
                if !Task.isCancelled { lookupError = error.localizedDescription; completed = query }
            }
        }
    }

    private func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    private struct QueryTask: Equatable {
        let query: ReviewQuery?
        let retry: Int
    }

    private var wordPicker: some View {
        HStack {
            Menu(term.isEmpty ? "选词缩小范围" : term) {
                ForEach(words.indices, id: \.self) { i in
                    Button(words[i]) { wordCount = 1; wordStart = i }
                }
            }.fixedSize()
            if let start = wordStart {
                if words.count - start > 1 {
                    Picker("词数", selection: $wordCount) {
                        ForEach(1...min(4, words.count-start), id: \.self) { Text("\($0) 词").tag($0) }
                    }.fixedSize()
                }
                Button("查整句") { wordStart = nil; wordCount = 1 }
            }
        }
    }

    @ViewBuilder private func results(_ result: ReviewHits) -> some View {
        if let s = result.spelling {
            Text(s.old == s.term ? "课件也出现了「\(s.term)」" : "待核对拼写　\(s.old) → \(s.term)")
                .font(.headline).textSelection(.enabled)
            Text(s.line).textSelection(.enabled)
            source(s.source)
        } else {
            Text("没有找到足够明确的拼写依据。").foregroundStyle(.secondary)
        }
        let others = result.pages.filter { $0.id != result.spelling?.source.id }
        if !others.isEmpty {
            Text("可能相关的页").font(.headline)
            ForEach(others) { page in source(page) }
        }
    }

    private func source(_ page: ReviewPage) -> some View {
        DisclosureGroup("\(page.name) · 第 \(page.page) 页") {
            VStack(alignment: .leading, spacing: 6) {
                Text(page.text).font(.callout).textSelection(.enabled)
                Button("打开原课件") {
                    if !NSWorkspace.shared.open(URL(fileURLWithPath: page.path)) {
                        lookupError = "原课件暂时无法打开。"
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
