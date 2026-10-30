import AppKit
import Combine
import SwiftUI

@MainActor
final class Workspace: ObservableObject {
    @Published var overview = Overview()
    @Published var selectedLecture: String?
    @Published var selectedVersion: String?
    @Published var transcript: Transcript?
    @Published var error: String?
    @Published var busy = false
    @Published var model = "asr-small"
    @Published var translate = true
    @Published var installedModels: [InstalledModel] = []
    @Published var downloading = false
    @Published var downloadMessage = ""
    @Published var downloadFraction: Double?
    @Published var downloadPaused = UserDefaults.standard.bool(forKey: "modelDownloadPaused")
    let backend = Backend()
    let live = LiveController()
    let subtitles = SubtitleWindow()
    private var subscriptions: Set<AnyCancellable> = []
    private var contentKey = ""
    private var recovered = false
    private var preparedModels = false

    init() {
        live.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &subscriptions)
        live.onLibraryChange = { [weak self] in Task { await self?.refresh() } }
    }

    var lecture: Lecture? {
        overview.courses.flatMap(\.lectures).first { $0.id == selectedLecture }
    }
    var activeTask: WorkTask? {
        lecture?.tasks.first { $0.version_id == selectedVersion } ?? lecture?.tasks.first
    }

    func refresh() async {
        do {
            overview = try JSONDecoder().decode(Overview.self, from: await backend.call([
                "library", "status"
            ]))
            if !recovered {
                recovered = true
                for lecture in overview.courses.flatMap(\.lectures) {
                    _ = try await backend.call(["library", "recover-recordings", lecture.id])
                }
                overview = try JSONDecoder().decode(Overview.self, from: await backend.call(["library", "status"]))
            }
            if selectedLecture == nil {
                selectedLecture = overview.courses.first?.lectures.first?.id
            }
            if let id = selectedLecture, !overview.courses.flatMap(\.lectures).contains(where: { $0.id == id }) {
                selectedLecture = overview.courses.first?.lectures.first?.id
                selectedVersion = nil
            }
            if let task = activeTask {
                let key = "\(task.version_id):\(task.next_unit):\(task.state):\(task.stale)"
                if key != contentKey {
                    transcript = try JSONDecoder().decode(Transcript.self, from: await backend.call([
                        "library", "content", task.version_id
                    ]))
                    contentKey = key
                }
            } else {
                transcript = nil
                contentKey = ""
            }
            let needsWorker = overview.courses.flatMap(\.lectures).flatMap(\.tasks)
                .contains { ["queued", "running"].contains($0.state) }
            if needsWorker && !live.flags.needsASR && !busy { try await backend.startWorker() }
        } catch { self.error = error.localizedDescription }
    }

    func refreshModels() async {
        let first = !preparedModels
        preparedModels = true
        do {
            if first, let previous = backend.paths.previousModels {
                downloadMessage = "正在复用本机已下载的模型…"
                _ = try await backend.call(["models", "import-cache", previous.path])
            }
            installedModels = try JSONDecoder().decode(ModelList.self, from: await backend.call(["models", "status"])).models
            if first && !downloadPaused {
                let missing = ["asr-small", "translate"].filter { !hasModel($0) }
                if !missing.isEmpty { Task { await self.installModels(missing) } }
            }
        }
        catch { self.error = error.localizedDescription }
    }

    func installModels(_ aliases: [String]) async {
        guard !downloading else { return }
        downloading = true; downloadPaused = false; downloadFraction = nil
        UserDefaults.standard.set(false, forKey: "modelDownloadPaused")
        downloadMessage = "正在后台准备本地模型（默认模型约 1.86 GB）…"
        let observer = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let file = self.backend.paths.storage.appendingPathComponent("model-download.json")
                if let data = try? Data(contentsOf: file),
                   let p = try? JSONDecoder().decode(ModelDownloadProgress.self, from: data) {
                    let name = p.model == "translate" ? "中文翻译" : p.model == "asr-large" ? "高精度识别" : "轻快识别"
                    self.downloadMessage = "正在下载\(name) · \(p.completed_models + 1)/\(p.total_models)"
                    if p.bytes_total > 0 {
                        self.downloadFraction = min(1, Double(p.bytes_completed) / Double(p.bytes_total))
                        self.downloadMessage += String(format: " · %.2f / %.2f GB", Double(p.bytes_completed) / 1e9, Double(p.bytes_total) / 1e9)
                    } else { self.downloadFraction = nil }
                }
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
        defer { observer.cancel(); downloading = false }
        do { try await backend.downloadModels(aliases); await refreshModels() }
        catch {
            if !downloadPaused { self.error = error.localizedDescription }
            await refreshModels()
        }
    }

    func cancelModelDownload() {
        downloadPaused = true
        UserDefaults.standard.set(true, forKey: "modelDownloadPaused")
        Task { await backend.cancelDownload() }
    }

    func hasModel(_ id: String) -> Bool { installedModels.contains { $0.id == id && $0.installed } }

    func perform(_ arguments: [String]) async {
        busy = true
        defer { busy = false }
        do {
            let data = try await backend.call(arguments)
            if arguments.prefix(2) == ["library", "recover-recordings"],
               let report = try JSONSerialization.jsonObject(with: data) as? [String: Any],
               let errors = report["errors"] as? [[String: String]], !errors.isEmpty {
                self.error = errors.prefix(3).map { ($0["directory"] ?? "") + "：" + ($0["message"] ?? "恢复未完成") }.joined(separator: "\n")
            }
            await refresh()
        } catch { self.error = error.localizedDescription }
    }

    func createCourse(_ name: String) async {
        await perform(["library", "create-course", name])
    }

    func createLecture(_ course: String, title: String) async {
        do {
            let created = try JSONDecoder().decode(Created.self, from: await backend.call([
                "library", "create-lecture", course, title
            ]))
            selectedLecture = created.id
            selectedVersion = nil
            await refresh()
        } catch { self.error = error.localizedDescription }
    }

    func importAudio(_ urls: [URL]) async {
        guard let lecture else { return }
        let accessed = urls.filter { $0.startAccessingSecurityScopedResource() }
        defer { accessed.forEach { $0.stopAccessingSecurityScopedResource() } }
        // File picker order is not promised. Start with a natural filename order;
        // the user can drag the managed segments into the intended sequence.
        let ordered = urls.sorted {
            $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending
        }
        await perform(["library", "import", lecture.id] + ordered.map(\.path))
    }

    func enqueue() async {
        guard let lecture else { return }
        var arguments = ["library", "enqueue", lecture.id, "--asr", model]
        if !translate { arguments.append("--no-translation") }
        selectedVersion = nil
        await perform(arguments)
    }

    func move(_ dragged: String, before destination: String) async {
        guard let lecture, dragged != destination else { return }
        var order = lecture.segments.map(\.id)
        guard let from = order.firstIndex(of: dragged) else { return }
        order.remove(at: from)
        guard let target = order.firstIndex(of: destination) else { return }
        order.insert(dragged, at: target)
        await perform(["library", "reorder", lecture.id] + order)
    }

    func export() async {
        guard let task = activeTask else { return }
        do {
            let result = try JSONDecoder().decode(Exported.self, from: await backend.call([
                "library", "export", task.version_id
            ]))
            NSWorkspace.shared.open(URL(fileURLWithPath: result.directory))
        } catch { self.error = error.localizedDescription }
    }
}
