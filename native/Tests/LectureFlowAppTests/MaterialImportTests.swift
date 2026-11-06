import AppKit
import CoreText
import LectureFlowCore
import XCTest
@testable import LectureFlow

final class MaterialImportTests: XCTestCase {
    private func urls(_ names: [String]) -> [URL] {
        names.map { URL(fileURLWithPath: "/synthetic/" + $0) }
    }
    private func receipt(_ name: String, duplicate: Bool = false) -> MaterialImportReceipt {
        MaterialImportReceipt(id: name, duplicate: duplicate)
    }
    private struct ImportFailure: LocalizedError {
        var errorDescription: String? { "合成损坏课件" }
    }

    @MainActor func testSuccessFailureDuplicateSuccessContinuesAndReportsEveryFile() async {
        let sources = urls(["first.pptx", "broken.pptx", "duplicate.pptx", "last.pptx"])
        var attempted: [URL] = [], progress: [Int] = []
        let report = await MaterialImportBatch.run(MaterialImportBatch.pending(sources), progress: { index, count, _ in
            XCTAssertEqual(count, 4); progress.append(index)
        }, importFile: { url in
            attempted.append(url)
            if url == sources[1] { throw ImportFailure() }
            return self.receipt(url.lastPathComponent, duplicate: url == sources[2])
        })
        XCTAssertEqual(attempted, sources)
        XCTAssertEqual(progress, [1, 2, 3, 4])
        XCTAssertEqual(report.items.map(\.outcome), [.imported, .failed("合成损坏课件"), .duplicate, .imported])
        XCTAssertEqual(report.retryItems.map(\.url), [sources[1]])
        XCTAssertEqual(report.summary, "已导入 2 份 · 重复跳过 1 份 · 失败 1 份")
    }

    @MainActor func testRetryOnlyUnfinishedPreservesEarlierSuccessAndDuplicate() async {
        let sources = urls(["first.pptx", "broken.pptx", "duplicate.pptx"])
        let original = await MaterialImportBatch.run(MaterialImportBatch.pending(sources), progress: { _, _, _ in }, importFile: {
            if $0 == sources[1] { throw ImportFailure() }
            return self.receipt($0.lastPathComponent, duplicate: $0 == sources[2])
        })
        var attempted: [URL] = []
        let retried = await MaterialImportBatch.run(original.items, progress: { index, count, _ in
            XCTAssertEqual(index, 1); XCTAssertEqual(count, 1)
        }, importFile: { url in attempted.append(url); return self.receipt("repaired") })
        XCTAssertEqual(attempted, [sources[1]])
        XCTAssertEqual(retried.items.map(\.id), original.items.map(\.id))
        XCTAssertEqual(retried.items.map(\.outcome), [.imported, .imported, .duplicate])
        XCTAssertTrue(retried.retryItems.isEmpty)
        _ = await MaterialImportBatch.run(retried.items, progress: { _, _, _ in XCTFail("Nothing remains") }, importFile: { _ in
            XCTFail("Completed files must never be resubmitted"); return self.receipt("unexpected")
        })
    }

    @MainActor func testCancellationStopsLaterFilesAndRetainsUnattemptedItemsForRetry() async {
        let sources = urls(["first.pptx", "interrupted.pptx", "last.pptx"])
        var attempted: [URL] = []
        let report = await MaterialImportBatch.run(MaterialImportBatch.pending(sources), progress: { _, _, _ in }, importFile: { url in
            attempted.append(url)
            if url == sources[1] { throw CancellationError() }
            return self.receipt("first")
        })
        XCTAssertEqual(attempted, Array(sources.prefix(2)))
        XCTAssertEqual(report.items.map(\.outcome), [.imported, .notAttempted, .notAttempted])
        XCTAssertEqual(report.retryItems.map(\.url), Array(sources.suffix(2)))
        XCTAssertEqual(report.summary, "已导入 1 份 · 未处理 2 份")
    }

    @MainActor func testAlreadyCancelledBatchNeverStartsAndEmptyBatchIsSafe() async {
        let sources = urls(["first.pptx"])
        let task = Task { @MainActor in
            await MaterialImportBatch.run(MaterialImportBatch.pending(sources), progress: { _, _, _ in XCTFail("Cancelled") }, importFile: { _ in
                XCTFail("Cancelled"); return self.receipt("unexpected")
            })
        }
        task.cancel()
        let report = await task.value
        XCTAssertEqual(report.items.map(\.outcome), [.notAttempted])
        let empty = await MaterialImportBatch.run([], progress: { _, _, _ in XCTFail("Empty") }, importFile: { _ in
            XCTFail("Empty"); return self.receipt("unexpected")
        })
        XCTAssertTrue(empty.items.isEmpty)
    }

    @MainActor func testCorruptPDFDoesNotCallBackendOrBlockNextFile() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let broken = directory.appendingPathComponent("broken.pdf")
        let original = Data("not a PDF".utf8)
        try original.write(to: broken)
        let next = directory.appendingPathComponent("next.pptx")
        var calls: [[String]] = []
        let report = await MaterialImportBatch.run(MaterialImportBatch.pending([broken, next]), progress: { _, _, _ in }, importFile: { url in
            try await MaterialFileImporter.run(url, courseID: "course", forceOCR: false, call: { args in
                calls.append(args); return Data(#"{"id":"saved"}"#.utf8)
            })
        })
        XCTAssertTrue(report.items[0].needsRetry)
        XCTAssertEqual(report.items[1].outcome, .imported)
        XCTAssertEqual(calls, [["materials", "import", "course", "course", next.path]])
        XCTAssertEqual(try Data(contentsOf: broken), original)
    }

    @MainActor func testPDFPagesFileExistsDuringCallAndIsRemovedOnSuccessAndFailure() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let source = directory.appendingPathComponent("synthetic.pdf")
        let consumer = try XCTUnwrap(CGDataConsumer(url: source as CFURL))
        var box = CGRect(x: 0, y: 0, width: 600, height: 800)
        let context = try XCTUnwrap(CGContext(consumer: consumer, mediaBox: &box, nil))
        context.beginPDFPage(nil)
        context.textPosition = CGPoint(x: 30, y: 700)
        let line = CTLineCreateWithAttributedString(NSAttributedString(string:
            "Synthetic course material: Kernel memory protection and process scheduling.",
            attributes: [.font: NSFont.systemFont(ofSize: 12)]))
        CTLineDraw(line, context)
        context.endPDFPage(); context.closePDF()
        let original = try Data(contentsOf: source)
        for fail in [false, true] {
            var pagesURL: URL?
            do {
                let result = try await MaterialFileImporter.run(source, courseID: "course", forceOCR: false, call: { args in
                    XCTAssertEqual(Array(args.prefix(5)), ["materials", "import", "course", "course", source.path])
                    XCTAssertEqual(args[5], "--pages-file")
                    let url = URL(fileURLWithPath: args[6]); pagesURL = url
                    let extracted = try JSONDecoder().decode(DocumentExtraction.self, from: Data(contentsOf: url))
                    XCTAssertEqual(extracted.pages.count, 1)
                    XCTAssertTrue(extracted.pages[0].text.contains("Kernel memory"))
                    if fail { throw ImportFailure() }
                    return Data(#"{"id":"saved","duplicate":true}"#.utf8)
                })
                XCTAssertFalse(fail)
                XCTAssertEqual(result.duplicate, true)
            } catch {
                XCTAssertTrue(fail); XCTAssertTrue(error is ImportFailure)
            }
            XCTAssertFalse(FileManager.default.fileExists(atPath: try XCTUnwrap(pagesURL).path))
        }
        XCTAssertEqual(try Data(contentsOf: source), original)
    }

    /// Real backend, synthetic files, isolated library. No model or microphone is used.
    @MainActor func testRealBackendBatchFailureRetryDedupAndSourcePreservation() async throws {
        let project = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let python = project.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw XCTSkip("Run uv sync --frozen --extra dev before backend integration tests")
        }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        func execute(_ arguments: [String]) throws -> Data {
            let process = Process(); process.executableURL = python
            process.arguments = arguments; process.currentDirectoryURL = project
            let output = Pipe(), errors = Pipe()
            process.standardOutput = output; process.standardError = errors
            try process.run()
            let result = output.fileHandleForReading.readDataToEndOfFile()
            let message = errors.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else {
                throw BackendError.unavailable(String(decoding: message, as: UTF8.self))
            }
            return result
        }
        let prefix = ["-m", "lectureflow.cli", "--library-root", directory.appendingPathComponent("library").path]
        func call(_ args: [String]) throws -> Data { try execute(prefix + args) }
        let created = try JSONDecoder().decode(Created.self, from: call(["library", "create-course", "Synthetic course"]))
        let sources = ["first.pptx", "broken.pptx", "last.pptx"].map { directory.appendingPathComponent($0) }
        func makeDeck(_ url: URL, text: String) throws {
            let script = """
            import sys, zipfile
            with zipfile.ZipFile(sys.argv[1], 'w') as z:
                z.writestr('ppt/presentation.xml', '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="r1"/></p:sldIdLst></p:presentation>')
                z.writestr('ppt/_rels/presentation.xml.rels', '<Relationships><Relationship Id="r1" Target="slides/slide1.xml"/></Relationships>')
                z.writestr('ppt/slides/slide1.xml', '<root xmlns:a="a"><a:t>' + sys.argv[2] + '</a:t></root>')
            """
            _ = try execute(["-c", script, url.path, text])
        }
        try makeDeck(sources[0], text: "Kernel memory protection")
        try Data("broken zip".utf8).write(to: sources[1])
        try makeDeck(sources[2], text: "Process scheduling algorithm")
        let original = try sources.map { try Data(contentsOf: $0) }
        var attempted: [URL] = []
        func importFile(_ url: URL) async throws -> MaterialImportReceipt {
            attempted.append(url)
            return try await MaterialFileImporter.run(url, courseID: created.id, forceOCR: false, call: { try call($0) })
        }
        let report = await MaterialImportBatch.run(MaterialImportBatch.pending(sources), progress: { _, _, _ in }, importFile: importFile)
        XCTAssertEqual(attempted, sources)
        XCTAssertEqual(report.items[0].outcome, .imported)
        XCTAssertTrue(report.items[1].needsRetry)
        XCTAssertEqual(report.items[2].outcome, .imported)
        XCTAssertEqual(try sources.map { try Data(contentsOf: $0) }, original)
        var listing = try JSONDecoder().decode(MaterialList.self, from: call(["materials", "list", "course", created.id]))
        XCTAssertEqual(listing.documents.count, 2)
        try makeDeck(sources[1], text: "Virtual address translation")
        attempted = []
        let retried = await MaterialImportBatch.run(report.items, progress: { _, _, _ in }, importFile: importFile)
        XCTAssertEqual(attempted, [sources[1]])
        XCTAssertTrue(retried.retryItems.isEmpty)
        let duplicates = await MaterialImportBatch.run(MaterialImportBatch.pending(sources), progress: { _, _, _ in }, importFile: importFile)
        XCTAssertEqual(duplicates.items.map(\.outcome), [.duplicate, .duplicate, .duplicate])
        listing = try JSONDecoder().decode(MaterialList.self, from: call(["materials", "list", "course", created.id]))
        XCTAssertEqual(listing.documents.count, 3)
    }
}
