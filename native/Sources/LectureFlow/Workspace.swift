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
            reviews.selectLecture(selectedLecture)
            if let id = selectedLecture {
                Task { [weak self] in
                    guard let self, self.selectedLecture == id, let lecture = self.lecture else { return }
                    await self.live.loadHistory(lecture: lecture, backend: self.backend)
                    if self.recovered { await self.recoverChinese(lecture) }
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
    @Published var reviewPresented = false
    @Published var priorityPresented = false
    @Published var subtitleSettingsPresented = false
    @Published var busy = false
    @Published private(set) var isExiting = false

    func prepareForExit() async {
        isExiting = true
        monitoring?.cancel(); monitoring = nil
        refreshToken = UUID()
        session.end(at: ProcessInfo.processInfo.systemUptime)
        await live.prepareForExit()
        await reviews.finishWrites()
        await backend.prepareForExit()
        persistSession()
    }
    func cancelExit() async {
        await backend.cancelExit()
        live.cancelExit()
        isExiting = false
        notice = "已取消退出；采集已结束，已有内容继续保存，不会自动开启麦克风。"
        startMonitoring()
    }
    @Published private(set) var model = RecognitionPreference.load()
    @Published var translate = true
    @Published var installedModels: [InstalledModel] = []
    @Published var downloading = false
    @Published var downloadMessage = ""
    @Published var downloadFraction: Double?
    @Published var downloadPaused = UserDefaults.standard.bool(forKey: "modelDownloadPaused")
    let backend = Backend()
    let reviews: ReviewMarks
    let playback = AudioPlayback()
    let live = LiveController()
    let subtitles = SubtitleWindow()
    private var subscriptions: Set<AnyCancellable> = []
    private var contentKey = ""
    private var recovered = false
    private var checkingChinese = Set<String>()
    private var privacyGeneration = 0
    private var preparedModels = false
    private var resultFence = ResultFence()
    private var refreshToken = UUID()
    private var monitoring: Task<Void, Never>?
    var revealMain: (() -> Void)?
    func showMainWindow() { revealMain?() }

    var isMainWindowVisible: Bool {
        NSApp.windows.contains { Self.isMainWindow($0) && $0.isVisible && !$0.isMiniaturized }
    }

    /// Floating caption control: show or hide the main library window.
    func toggleMainWindow() {
        if isMainWindowVisible {
            hideMainWindow()
        } else if let window = NSApp.windows.first(where: Self.isMainWindow) {
            // orderOut keeps the window in the list; bring it back without openWindow.
            window.deminiaturize(nil)
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            objectWillChange.send()
        } else {
            showMainWindow()
            objectWillChange.send()
        }
    }

    init() {
        reviews = ReviewMarks(backend: backend)
        reviews.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &subscriptions)
        playback.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &subscriptions)
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
        guard !isExiting else { return }
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

    var floatingSubtitlesVisible: Bool { subtitles.isVisible }

    var currentReviewCaption: LiveCaption? {
        if live.isCapturing && !live.flags.needsASR { return nil }
        guard let caption = live.displayCaptions.last, caption.session_id != nil else { return nil }
        // A new recording can precede its first caption; never mark a previous session then.
        if let anchor = live.currentReviewAnchor, caption.session_id != anchor.captureID { return nil }
        return caption
    }

    func isReviewMarked(_ caption: LiveCaption) -> Bool {
        !reviews.matching(caption, in: live.displayCaptions).isEmpty
    }

    func markCurrentForReview() {
        if let caption = currentReviewCaption, let lecture = selectedLecture {
            markCaptionForReview(caption, lecture: lecture); return
        }
        guard !isExiting, let lecture = live.lectureID,
              let anchor = live.currentReviewAnchor else { return }
        reviews.add(lecture: lecture, capture: anchor.captureID, time: anchor.time)
    }

    func markCaptionForReview(_ caption: LiveCaption, lecture: String) {
        guard !isExiting, selectedLecture == lecture else { return }
        reviews.toggle(caption, in: live.displayCaptions, lecture: lecture)
    }

    func openReviewMarks() {
        if !reviewPresented { reviews.prepareForPresentation() }
        playback.stop()
        reviewPresented = true
        if !isMainWindowVisible { toggleMainWindow() }
    }

    func openSubtitles(hideMain: Bool = false) {
        subtitles.show(workspace: self)
        if hideMain { hideMainWindow() }
    }

    /// Header control: show when hidden, hide when already visible.
    func toggleSubtitles() {
        subtitles.toggle(workspace: self)
        objectWillChange.send()
    }

    func hideSubtitles() {
        subtitles.hide()
        objectWillChange.send()
    }

    func hideMainWindow() {
        for window in NSApp.windows where Self.isMainWindow(window) {
            window.orderOut(nil)
        }
        objectWillChange.send()
    }

    static func isMainWindow(_ window: NSWindow) -> Bool {
        if window.identifier?.rawValue == "LectureFlowMain" { return true }
        if window is NSPanel { return false }
        return window.title == "LectureFlow"
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
        privacyGeneration += 1
        if let data = try? JSONEncoder().encode(flags) { UserDefaults.standard.set(data, forKey: "capturePreferences") }
        notice = nil
    }
    var effectiveCapture: CaptureFlags {
        session.desired.available(asr: hasModel(model), translation: hasModel("translate"))
    }
    var captionsWaiting: Bool { session.desired.needsASR && effectiveCapture != session.desired }
    func primarySessionAction(subtitlesIfIdle: Bool = false) {
        guard !isExiting else { return }
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
        guard !isExiting else { return }
        guard session.state == .running, let lecture, effectiveCapture.needsASR else { return }
        live.change(effectiveCapture, lecture: lecture, backend: backend, model: model)
    }
    private func reconcileCapture() {
        guard !isExiting else { return }
        guard session.state == .running, live.flags.needsMicrophone, !live.recoveryExhausted,
              let lecture, effectiveCapture != live.flags, effectiveCapture.needsMicrophone else { return }
        live.change(effectiveCapture, lecture: lecture, backend: backend, model: model)
        if !captionsWaiting { notice = "本地模型已就绪，字幕已开启；此前录音仍保留。" }
    }
    func selectRecognitionModel(_ id: String) {
        guard !live.isCapturing else { notice = "请先暂停，再切换识别模型。"; return }
        guard ["asr-small", "asr-large"].contains(id) else { return }
        guard hasModel(id) else { notice = "该模型尚未下载，可在模型面板中下载后切换。"; return }
        RecognitionPreference.save(id)
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

    private func loadOverview() async throws -> Overview {
        try JSONDecoder().decode(Overview.self, from: await backend.call(["library", "status"]))
    }

    private func permitsChineseRecovery(_ id: String) -> Bool {
        let defaults = UserDefaults.standard
        if let data = defaults.data(forKey: "capturePreferences"),
           (try? JSONDecoder().decode(CaptureFlags.self, from: data)) != .all { return false }
        if selectedLecture == id { return session.desired == .all }
        guard let data = defaults.data(forKey: "classroomSession." + id) else { return true }
        return (try? JSONDecoder().decode(ClassroomSession.self, from: data))?.desired == .all
    }

    private func recoverChinese(_ lecture: Lecture) async {
        guard !isExiting, !live.flags.needsMicrophone, !checkingChinese.contains(lecture.id) else { return }
        guard permitsChineseRecovery(lecture.id) else {
            if selectedLecture == lecture.id && live.captions.contains(where: {
                $0.status == "temporary" && !$0.english.isEmpty && $0.chinese.isEmpty
            }) { notice = "当前保存设置不允许补译中断的临时字幕。" }
            return
        }
        checkingChinese.insert(lecture.id)
        defer { checkingChinese.remove(lecture.id) }
        let generation = privacyGeneration
        do {
            let prepared = try await backend.call(["live", "prepare-missing-chinese", lecture.id])
            struct Preview: Decodable { struct Item: Decodable {}; let items: [Item]; let unavailable: Int }
            let preview = try JSONDecoder().decode(Preview.self, from: prepared)
            guard !isExiting, !live.flags.needsMicrophone, generation == privacyGeneration,
                  permitsChineseRecovery(lecture.id) else { return }
            if !preview.items.isEmpty {
                struct Applied: Decodable { let applied: Int }
                let applied = try JSONDecoder().decode(Applied.self, from: await backend.call(
                    ["live", "apply-missing-chinese", lecture.id], input: prepared))
                if selectedLecture == lecture.id && applied.applied > 0 {
                    await live.loadHistory(lecture: lecture, backend: backend)
                    notice = "已为中断的临时字幕补上中文，共 " + String(applied.applied) + " 段。"
                }
            }
            if selectedLecture == lecture.id && preview.unavailable > 0 {
                notice = "部分临时字幕缺少可核对的双语保存范围或完整原音，未自动补译。"
            }
        } catch {
            if selectedLecture == lecture.id && !isExiting {
                notice = "此次未能补译中断的字幕；重开此讲次可重试。"
            }
        }
    }

    func refresh() async {
        guard !isExiting else { return }
        let request = UUID(); refreshToken = request
        do {
            let latest = try await loadOverview()
            guard !isExiting, refreshToken == request else { return }
            overview = latest
            refreshError = nil
            if !recovered {
                recovered = true
                for lecture in overview.courses.flatMap(\.lectures) {
                    _ = try await backend.call(["library", "recover-recordings", lecture.id])
                }
                let classrooms = overview.courses.flatMap(\.lectures)
                Task { for lecture in classrooms { await recoverChinese(lecture) } }
                let recoveredState = try await loadOverview()
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
            if !isExiting && (needsWorker || needsKnowledge) && !live.flags.needsASR && !busy { try await backend.startWorker() }
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

    var course: Course? {
        overview.courses.first { $0.id == selectedCourse }
    }

    func refreshModels() async {
        guard !isExiting else { return }
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
                let missing = [model, "translate"].filter { !hasModel($0) }
                if !missing.isEmpty { Task { await self.installModels(missing) } }
            }
        }
        catch { self.error = error.localizedDescription }
    }

    func installModels(_ aliases: [String]) async {
        guard !isExiting else { return }
        guard !downloading else { return }
        downloading = true; downloadPaused = false; downloadFraction = nil
        UserDefaults.standard.set(false, forKey: "modelDownloadPaused")
        downloadMessage = "正在后台准备所选本地模型…"
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
        guard !isExiting else { return }
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
        var arguments = ["library", "create-lecture", course]
        if let name = title?.trimmingCharacters(in: .whitespacesAndNewlines), !name.isEmpty {
            arguments.append(name)
        }
        do {
            let created = try JSONDecoder().decode(Created.self, from: await backend.call(arguments))
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
        guard !isExiting, selectedCourse == courseID, selectedLecture == id else { return }
        // A polling refresh can supersede createLecture's refresh before it publishes the new row.
        if lecture == nil {
            do {
                let snapshot = try await loadOverview()
                guard !isExiting, selectedCourse == courseID, selectedLecture == id else { return }
                if lecture == nil {
                    guard let created = snapshot.courses.first(where: { $0.id == courseID })?.lectures.first(where: { $0.id == id }),
                          let index = overview.courses.firstIndex(where: { $0.id == courseID }) else {
                        error = "无法读取刚创建的 Lecture，采集尚未开始。"
                        return
                    }
                    overview.courses[index].lectures.append(created)
                }
            } catch {
                self.error = "读取新 Lecture 失败，采集尚未开始：" + error.localizedDescription
                return
            }
        }
        primarySessionAction()
    }

    func importAudio(_ urls: [URL], intoLecture lectureID: String? = nil, creatingIn courseID: String? = nil) async {
        guard !urls.isEmpty, let target = courseID ?? lectureID ?? selectedLecture else { return }
        let accessed = urls.filter { $0.startAccessingSecurityScopedResource() }
        defer { accessed.forEach { $0.stopAccessingSecurityScopedResource() } }
        let ordered = urls.sorted {
            $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending
        }
        if let courseID {
            busy = true
            defer { busy = false }
            do {
                let arguments = ["library", "import-new", courseID] + ordered.map(\.path)
                let created = try JSONDecoder().decode(Created.self, from: await backend.call(arguments))
                await refresh()
                if selectedCourse == courseID, selectedLecture == nil, !live.flags.needsMicrophone {
                    selectLecture(created.id)
                    requestedTab = "audio"
                }
            } catch { self.error = error.localizedDescription }
        } else {
            await perform(["library", "import", target] + ordered.map(\.path))
            if selectedLecture == target { requestedTab = "audio" }
        }
    }

    func enqueue() async {
        guard let lecture else { return }
        var arguments = ["library", "enqueue", lecture.id, "--asr", model,
                         "--language", SourceLanguagePreference.load()]
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
