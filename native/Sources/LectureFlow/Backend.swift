import Foundation

struct Overview: Decodable, Sendable {
    var courses: [Course] = []
}
struct Course: Decodable, Identifiable, Sendable {
    let id: String
    let name: String
    let lectures: [Lecture]
}
struct Lecture: Decodable, Identifiable, Sendable {
    let id: String
    let title: String
    let path: String
    let segments: [AudioSegment]
    let tasks: [WorkTask]
}
struct AudioSegment: Decodable, Identifiable, Sendable {
    let id: String
    let original_name: String
    let duration: Double
    let files: [String]
}
struct WorkTask: Decodable, Identifiable, Sendable {
    let id: String
    let version_id: String
    let state: String
    let next_unit: Int
    let total_units: Int
    let error: String?
    let stale: Int
    let scope: String
    let segment_count: Int
    let asr: String
    var scopeLabel: String { scope == "lecture" ? "整堂课" : "\(segment_count) 段录音" }
    var modelLabel: String { asr == "asr-large" ? "高精度" : "轻快" }
    var fraction: Double { Double(next_unit) / Double(max(1, total_units)) }
    var label: String {
        switch state {
        case "queued": return "等待处理"
        case "running": return "正在转写与翻译"
        case "paused": return "已暂停"
        case "cancelled": return "已取消"
        case "failed": return "处理失败"
        case "completed": return "已完成"
        default: return state
        }
    }
}
struct Transcript: Decodable, Sendable {
    let version_id: String
    let stale: Bool
    let units: [TranscriptUnit]
}
struct TranscriptUnit: Decodable, Identifiable, Sendable {
    let sequence: Int
    let logical_start: Double
    let english: String
    let chinese: String
    var id: Int { sequence }
}
struct Created: Decodable, Sendable { let id: String }
struct Exported: Decodable, Sendable { let directory: String }

enum BackendError: LocalizedError {
    case unavailable(String)
    var errorDescription: String? {
        switch self { case .unavailable(let message): return message }
    }
}

final class DownloadLifetime: @unchecked Sendable {
    private let lock = NSLock()
    private var child: Process?
    private var closed = false
    func launch(_ process: Process) throws {
        lock.lock(); defer { lock.unlock() }
        guard !closed else { throw CancellationError() }
        try process.run(); child = process
    }
    func stop(shutdown: Bool = false) {
        lock.lock(); defer { lock.unlock() }
        if shutdown { closed = true }
        if child?.isRunning == true { child?.terminate() }
    }
}

actor Backend {
    let paths = RuntimePaths()
    nonisolated let downloadLifetime = DownloadLifetime()
    private var worker: Process?
    private var summaries: [String: Process] = [:]
    private var download: Process?
    private var lastWorkerAttempt = Date.distantPast

    func call(_ arguments: [String], input: Data? = nil) throws -> Data {
        let child = try paths.process(arguments)
        let output = Pipe()
        let errorURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("lectureflow-\(UUID().uuidString).log")
        FileManager.default.createFile(atPath: errorURL.path, contents: nil)
        let errors = try FileHandle(forWritingTo: errorURL)
        defer {
            try? errors.close()
            try? FileManager.default.removeItem(at: errorURL)
        }
        child.standardOutput = output
        child.standardError = errors
        let pipe = Pipe()
        if input != nil { child.standardInput = pipe }
        try child.run()
        if let input {
            try pipe.fileHandleForWriting.write(contentsOf: input)
            try pipe.fileHandleForWriting.close()
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        child.waitUntilExit()
        if child.terminationStatus != 0 {
            let message = (try? String(contentsOf: errorURL, encoding: .utf8)) ?? "本地处理失败"
            throw BackendError.unavailable(String(message.suffix(4000)))
        }
        return data
    }

    func startWorker(force: Bool = false) throws {
        if worker?.isRunning == true { return }
        if !force && Date().timeIntervalSince(lastWorkerAttempt) < 10 { return }
        lastWorkerAttempt = Date()
        let handle = try paths.log("worker")
        defer { try? handle.close() }
        let child = try paths.process(["worker"])
        child.standardOutput = handle
        child.standardError = handle
        try child.run()
        worker = child
        // The worker is a separate process. Window closure never cancels its task.
    }

    func startSummary(_ id: String, apiKey: String, digest: String) throws {
        if summaries[id]?.isRunning == true { return }
        let child = try paths.process(["summary", "run", id])
        let input = Pipe()
        let log = try paths.log("summary")
        defer { try? log.close() }
        child.standardInput = input
        child.standardOutput = log
        child.standardError = log
        try child.run()
        let data = try JSONSerialization.data(withJSONObject: ["api_key": apiKey, "digest": digest])
        try input.fileHandleForWriting.write(contentsOf: data)
        try input.fileHandleForWriting.close()
        summaries = summaries.filter { $0.value.isRunning }
        summaries[id] = child
    }

    func downloadModels(_ aliases: [String]) async throws {
        guard download?.isRunning != true else { return }
        let progress = paths.storage.appendingPathComponent("model-download.json")
        let child = try paths.process(["fetch", "--progress-file", progress.path] + aliases)
        let log = try paths.log("download")
        defer { try? log.close() }
        child.standardOutput = log; child.standardError = log
        download = child
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            child.terminationHandler = { process in
                if process.terminationStatus == 0 { continuation.resume() }
                else {
                    continuation.resume(throwing: BackendError.unavailable(
                        "模型下载未完成，可稍后重试。下载过程不读取课堂资料。"))
                }
            }
            do { try downloadLifetime.launch(child) }
            catch { continuation.resume(throwing: error) }
        }
        download = nil
    }

    func cancelDownload() { downloadLifetime.stop() }
}
