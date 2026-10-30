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

actor Backend {
    let root: URL
    private var worker: Process?
    private var lastWorkerAttempt = Date.distantPast

    init() {
        let path = ProcessInfo.processInfo.environment["LECTUREFLOW_PROJECT_ROOT"]
            ?? Bundle.main.object(forInfoDictionaryKey: "LectureFlowProjectRoot") as? String
            ?? FileManager.default.currentDirectoryPath
        root = URL(fileURLWithPath: path)
    }

    private func process(_ arguments: [String]) throws -> Process {
        let python = root.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw BackendError.unavailable("项目运行环境尚未安装，请先运行项目中的 scripts/dev。")
        }
        let child = Process()
        child.executableURL = python
        child.currentDirectoryURL = root
        child.arguments = ["-m", "lectureflow.cli", "--model-root",
                           root.appendingPathComponent("models").path, "--library-root",
                           root.appendingPathComponent("data/library").path] + arguments
        child.standardInput = FileHandle.nullDevice
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        child.environment = environment
        return child
    }

    func call(_ arguments: [String]) throws -> Data {
        let child = try process(arguments)
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
        try child.run()
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
        let logs = root.appendingPathComponent("reports/private")
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let log = logs.appendingPathComponent("worker.log")
        if !FileManager.default.fileExists(atPath: log.path) {
            FileManager.default.createFile(atPath: log.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: log)
        try handle.seekToEnd()
        defer { try? handle.close() }
        let child = try process(["worker"])
        child.standardOutput = handle
        child.standardError = handle
        try child.run()
        worker = child
        // The worker is a separate process. Window closure never cancels its task.
    }
}
