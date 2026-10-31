import AppKit
import Combine
import LectureFlowCore
import SwiftUI

@MainActor
final class Workspace: ObservableObject {
    @Published var overview = Overview()
    @Published private(set) var selectedCourse: String?
    @Published private(set) var selectedLecture: String? {
        willSet { if newValue != selectedLecture { persistSession() } }
        didSet {
            guard oldValue != selectedLecture else { return }
            refreshToken = UUID()
            resultFence.invalidate(); transcript = nil; contentKey = ""
            selectedVersion = nil
            if let id = selectedLecture,
               let course = overview.courses.first(where: { $0.lectures.contains { $0.id == id } }) {
                selectedCourse = course.id
            }
            restoreSession()
            live.prepareHistory(for: selectedLecture)
            if let id = selectedLecture {
                Task { [weak self] in
                    guard let self, self.selectedLecture == id, let lecture = self.lecture else { return }
                    await self.live.loadHistory(lecture: lecture, backend: self.backend)
                }
            }
        }
    }
    @Published var selectedVersion: String? {
        didSet {
            guard oldValue != selectedVersion else { return }
            resultFence.invalidate(); transcript = nil; contentKey = ""
        }
    }
    @Published var transcript: Transcript?
    @Published var error: String?
    @Published private(set) var session = ClassroomSession() {
        didSet { persistSession() }
    }
    @Published var notice: String?
    @Published private(set) var refreshError: String?
    @Published var requestedTab: String?
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
    private var resultFence = ResultFence()
    private var refreshToken = UUID()
    private var monitoring: Task<Void, Never>?
    var revealMain: (() -> Void)?
    func showMainWindow() { revealMain?() }

    init() {
        // Meter updates redraw the recorder itself, not the entire library and task list.
        Publishers.MergeMany(
            live.$flags.dropFirst().map { _ in () }.eraseToAnyPublisher(),
            live.$error.dropFirst().map { _ in () }.eraseToAnyPublisher(),
            live.$isCapturing.dropFirst().map { _ in () }.eraseToAnyPublisher()
        ).sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &subscriptions)
        live.onLibraryChange = { [weak self] in Task { await self?.refresh() } }
        live.$isCapturing.dropFirst().sink { [weak self] active in
            guard let self else { return }
            if active { self.session.started(at: ProcessInfo.processInfo.systemUptime) }
            else { self.session.pause(at: ProcessInfo.processInfo.systemUptime) }
        }.store(in: &subscriptions)
        live.$flags.dropFirst().sink { [weak self] flags in
            guard let self, !flags.needsMicrophone else { return }
            self.session.pause(at: ProcessInfo.processInfo.systemUptime)
            self.notice = nil
        }.store(in: &subscriptions)
        live.$flags.map(\.needsASR).removeDuplicates().dropFirst().sink { [weak self] enabled in
            guard enabled, let self else { return }
            if UserDefaults.standard.object(forKey: "autoFloatingSubtitles") == nil
                || UserDefaults.standard.bool(forKey: "autoFloatingSubtitles") {
                self.openSubtitles()
            }
        }.store(in: &subscriptions)
    }

    func startMonitoring() {
        guard monitoring == nil else { return }
        monitoring = Task { [weak self] in
            // Library first so the sidebar isn't blocked behind a slow model probe.
            await self?.refresh()
            await self?.refreshModels()
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    func openSubtitles(hideMain: Bool = false) {
        subtitles.show(workspace: self)
        if hideMain {
            for window in NSApp.windows where window.identifier?.rawValue == "LectureFlowMain" {
                window.orderOut(nil)
            }
        }
    }

    private func persistSession() {
        guard let id = selectedLecture,
              let data = try? JSONEncoder().encode(session.checkpoint(at: ProcessInfo.processInfo.systemUptime)) else { return }
        UserDefaults.standard.set(data, forKey: "classroomSession." + id)
    }
    private func restoreSession() {
        if let id = selectedLecture, let data = UserDefaults.standard.data(forKey: "classroomSession." + id),
           let saved = try? JSONDecoder().decode(ClassroomSession.self, from: data) {
            session = saved.restored()
        } else {
            var fresh = ClassroomSession()
            if let data = UserDefaults.standard.data(forKey: "capturePreferences"),
               let flags = try? JSONDecoder().decode(CaptureFlags.self, from: data) { _ = fresh.configure(flags) }
            session = fresh
        }
        notice = nil
    }
    func configureSession(_ flags: CaptureFlags) {
        guard session.configure(flags) else { notice = "请先暂停；至少选择一种记录方式。"; return }
        if let data = try? JSONEncoder().encode(flags) { UserDefaults.standard.set(data, forKey: "capturePreferences") }
        notice = nil
    }
    var effectiveCapture: CaptureFlags {
        session.desired.available(asr: hasModel(model), translation: hasModel("translate"))
    }
    var captionsWaiting: Bool { session.desired.needsASR && effectiveCapture != session.desired }
    func primarySessionAction(subtitlesIfIdle: Bool = false) {
        if session.state == .running || session.state == .preparing { pauseSession(); return }
        guard !busy, let lecture else { return }
        if subtitlesIfIdle && !session.hasSession { _ = session.configure(CapturePreset.subtitles.flags) }
        let flags = effectiveCapture
        guard flags.needsMicrophone else {
            notice = "字幕模型尚未就绪。请继续下载，或选择“仅录音”先保存课堂。"; return
        }
        guard session.begin() else { return }
        notice = captionsWaiting ? "录音先开始，模型就绪后自动开启字幕；这期间的录音可课后转写。" : nil
        live.change(flags, lecture: lecture, backend: backend, model: model)
        requestedTab = session.desired.needsASR ? "transcript" : "audio"
    }
    func pauseSession() {
        session.pause(at: ProcessInfo.processInfo.systemUptime)
        live.shutdown(); notice = "已暂停；继续后仍保存在本堂课，使用原来的记录方式。"
    }
    func endSession() {
        session.end(at: ProcessInfo.processInfo.systemUptime)
        live.endSession(); notice = nil
        Task { await refresh() }
    }
    func reconnectCaptions() {
        guard session.state == .running, let lecture, effectiveCapture.needsASR else { return }
        live.change(effectiveCapture, lecture: lecture, backend: backend, model: model)
    }
    private func reconcileCapture() {
        guard session.state == .running, live.flags.needsMicrophone,
              let lecture, effectiveCapture != live.flags, effectiveCapture.needsMicrophone else { return }
        live.change(effectiveCapture, lecture: lecture, backend: backend, model: model)
        if !captionsWaiting { notice = "本地模型已就绪，字幕已开启；此前录音仍保留。" }
    }
    func selectRecognitionModel(_ id: String) {
        guard hasModel(id) else { notice = "该模型尚未下载，可在模型面板中下载后切换。"; return }
        model = id
        live.selectRecognitionModel(id)
    }
    func downloadAndSelectModel(_ id: String) async {
        guard !downloading else { notice = "其他模型正在下载，完成后可下载此模型。"; return }
        await installModels([id])
        if hasModel(id) { selectRecognitionModel(id) }
    }

    var lecture: Lecture? {
        overview.courses.flatMap(\.lectures).first { $0.id == selectedLecture }
    }
    var activeTask: WorkTask? {
        lecture?.tasks.first { $0.version_id == selectedVersion } ?? lecture?.tasks.first
    }

    func refresh() async {
        let request = UUID(); refreshToken = request
        do {
            let latest = try JSONDecoder().decode(Overview.self, from: await backend.call([
                "library", "status"
            ]))
            guard refreshToken == request else { return }
            overview = latest
            refreshError = nil
            if !recovered {
                recovered = true
                for lecture in overview.courses.flatMap(\.lectures) {
                    _ = try await backend.call(["library", "recover-recordings", lecture.id])
                }
                let recoveredState = try JSONDecoder().decode(Overview.self, from: await backend.call(["library", "status"]))
                guard refreshToken == request else { return }
                overview = recoveredState
            }
            if let courseID = selectedCourse, !overview.courses.contains(where: { $0.id == courseID }) {
                selectedCourse = overview.courses.first?.id
            }
            if selectedCourse == nil {
                selectedCourse = overview.courses.first?.id
            }
            if let id = selectedLecture, !overview.courses.flatMap(\.lectures).contains(where: { $0.id == id }) {
                selectedLecture = nil
                selectedVersion = nil
            }
            if let task = activeTask {
                let key = "\(task.version_id):\(task.next_unit):\(task.state):\(task.stale)"
                if key != contentKey {
                    guard let owner = selectedLecture else { return }
                    let ticket = resultFence.ticket(owner: owner, version: task.version_id)
                    let contentRequest = refreshToken
                    let loaded = try JSONDecoder().decode(Transcript.self, from: await backend.call([
                        "library", "content", task.version_id
                    ]))
                    guard refreshToken == contentRequest,
                          resultFence.accepts(ticket, owner: selectedLecture, version: activeTask?.version_id),
                          loaded.lecture_id == owner else { return }
                    transcript = loaded
                    contentKey = key
                }
            } else {
                transcript = nil
                contentKey = ""
            }
            let needsWorker = overview.courses.flatMap(\.lectures).flatMap(\.tasks)
                .contains { ["queued", "running"].contains($0.state) }
            let needsKnowledge = hasModel("translate") && (overview.knowledge_jobs ?? []).contains { ["queued", "running"].contains($0.state) }
            if (needsWorker || needsKnowledge) && !live.flags.needsASR && !busy { try await backend.startWorker() }
            persistSession()
        } catch { if refreshToken == request { refreshError = error.localizedDescription } }
    }

    func selectCourse(_ id: String?) {
        selectedCourse = id
        if let id, let lecture = selectedLecture,
           overview.courses.first(where: { $0.id == id })?.lectures.contains(where: { $0.id == lecture }) != true {
            selectLecture(nil)
        }
    }

    func selectLecture(_ id: String?) {
        if live.flags.needsMicrophone && id != live.lectureID {
            error = "请先暂停当前 Lecture，再切换。"; return
        }
        selectedLecture = id
    }

    func nextLectureTitle(for courseID: String) -> String {
        let count = overview.courses.first { $0.id == courseID }?.lectures.count ?? 0
        return String(format: "Lecture %02d", count + 1)
    }

    var course: Course? {
        overview.courses.first { $0.id == selectedCourse }
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
            reconcileCapture()
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

    func createLecture(_ course: String, title: String? = nil, select: Bool = true) async -> String? {
        let name = (title?.trimmingCharacters(in: .whitespacesAndNewlines)).flatMap { $0.isEmpty ? nil : $0 }
            ?? nextLectureTitle(for: course)
        do {
            let created = try JSONDecoder().decode(Created.self, from: await backend.call([
                "library", "create-lecture", course, name
            ]))
            selectedCourse = course
            if select && !live.flags.needsMicrophone {
                selectedLecture = created.id
                selectedVersion = nil
            }
            await refresh()
            return created.id
        } catch {
            self.error = error.localizedDescription
            return nil
        }
    }

    func startNewLecture(in courseID: String) async {
        guard !live.flags.needsMicrophone else {
            error = "请先暂停当前采集，再开始新 Lecture。"
            return
        }
        guard let id = await createLecture(courseID) else { return }
        selectedLecture = id
        primarySessionAction()
    }

    func importAudio(_ urls: [URL], intoLecture lectureID: String? = nil) async {
        let targetID = lectureID ?? selectedLecture
        guard let targetID else { return }
        let accessed = urls.filter { $0.startAccessingSecurityScopedResource() }
        defer { accessed.forEach { $0.stopAccessingSecurityScopedResource() } }
        // File picker order is not promised. Start with a natural filename order;
        // the user can drag the managed segments into the intended sequence.
        let ordered = urls.sorted {
            $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending
        }
        await perform(["library", "import", targetID] + ordered.map(\.path))
        if selectedLecture == targetID { requestedTab = "audio" }
    }

    /// Creates a Lecture only after the user confirms file selection (cancel leaves no empty record).
    func importAudioCreatingLecture(courseID: String, urls: [URL]) async {
        guard !urls.isEmpty else { return }
        guard let id = await createLecture(courseID) else { return }
        await importAudio(urls, intoLecture: id)
        selectedLecture = id
        requestedTab = "audio"
    }

    func enqueue() async {
        guard let lecture else { return }
        var arguments = ["library", "enqueue", lecture.id, "--asr", model]
        if !translate { arguments.append("--no-translation") }
        selectedVersion = nil
        await perform(arguments)
        if selectedLecture == lecture.id { requestedTab = "transcript" }
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
