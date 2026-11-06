import Foundation
import LectureFlowCore

struct MaterialImportReceipt: Decodable {
    let id: String
    let duplicate: Bool?
}

struct MaterialImportItem: Identifiable {
    enum Outcome: Equatable {
        case imported, duplicate, failed(String), notAttempted
    }
    let id: UUID
    let url: URL
    var outcome: Outcome

    var needsRetry: Bool {
        switch outcome {
        case .failed, .notAttempted: return true
        default: return false
        }
    }
    var detail: String {
        switch outcome {
        case .imported: return "已导入"
        case .duplicate: return "已存在，已跳过"
        case .failed(let message): return message
        case .notAttempted: return "导入已中断，尚未处理"
        }
    }
}

struct MaterialImportReport {
    var items: [MaterialImportItem]
    var retryItems: [MaterialImportItem] { items.filter(\.needsRetry) }
    var summary: String {
        let imported = items.filter { $0.outcome == .imported }.count
        let duplicates = items.filter { $0.outcome == .duplicate }.count
        let pending = items.filter { $0.outcome == .notAttempted }.count
        let failed = retryItems.count - pending
        var parts = ["已导入 \(imported) 份"]
        if duplicates > 0 { parts.append("重复跳过 \(duplicates) 份") }
        if failed > 0 { parts.append("失败 \(failed) 份") }
        if pending > 0 { parts.append("未处理 \(pending) 份") }
        return parts.joined(separator: " · ")
    }
}

/// A bad document must not prevent later documents from being attempted.
/// Retry keeps the original order and never resubmits successful/duplicate items.
enum MaterialImportBatch {
    @MainActor
    static func run(
        _ items: [MaterialImportItem],
        progress: (Int, Int, URL) -> Void,
        importFile: (URL) async throws -> MaterialImportReceipt
    ) async -> MaterialImportReport {
        var report = MaterialImportReport(items: items)
        let indices = report.items.indices.filter { report.items[$0].needsRetry }
        for (position, index) in indices.enumerated() {
            guard !Task.isCancelled else { break }
            let url = report.items[index].url
            progress(position + 1, indices.count, url)
            do {
                let receipt = try await importFile(url)
                report.items[index].outcome = receipt.duplicate == true ? .duplicate : .imported
            } catch is CancellationError {
                // Cancellation is not a corrupt-file error. Leave unfinished work retryable.
                break
            } catch {
                report.items[index].outcome = .failed(error.localizedDescription)
            }
        }
        return report
    }

    static func pending(_ urls: [URL]) -> [MaterialImportItem] {
        urls.map { MaterialImportItem(id: UUID(), url: $0, outcome: .notAttempted) }
    }
}

/// The same single-file path is used for initial import and failed-only retries.
enum MaterialFileImporter {
    @MainActor
    static func run(
        _ url: URL, courseID: String, forceOCR: Bool,
        call: ([String]) async throws -> Data,
        extractPDF: @Sendable @escaping (URL, Bool) throws -> DocumentExtraction = {
            try DocumentExtractor.pdf($0, forceOCR: $1)
        }
    ) async throws -> MaterialImportReceipt {
        try Task.checkCancellation()
        let accessed = url.startAccessingSecurityScopedResource()
        defer { if accessed { url.stopAccessingSecurityScopedResource() } }
        var arguments = ["materials", "import", "course", courseID, url.path]
        let temporary = FileManager.default.temporaryDirectory
            .appendingPathComponent("lectureflow-pages-\(UUID().uuidString).json")
        defer { try? FileManager.default.removeItem(at: temporary) }
        if url.pathExtension.lowercased() == "pdf" {
            let extraction = try await Task.detached(priority: .userInitiated) {
                try extractPDF(url, forceOCR)
            }.value
            try Task.checkCancellation()
            try JSONEncoder().encode(extraction).write(to: temporary, options: .atomic)
            arguments += ["--pages-file", temporary.path]
        }
        try Task.checkCancellation()
        return try JSONDecoder().decode(MaterialImportReceipt.self, from: await call(arguments))
    }
}
