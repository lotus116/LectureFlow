import AppKit
import AVFoundation
import LectureFlowCore
import SwiftUI

struct LiveHistory: Decodable { let captions: [LiveCaption] }

private final class LivePipe {
    private let process: Process
    private let input = Pipe()
    private let output = Pipe()
    private let writes = DispatchQueue(label: "LectureFlow.inference-input")
    private let reads = DispatchQueue(label: "LectureFlow.inference-output")
    private let lock = NSLock()
    private var pending = 0
    private var accumulated = Data()
    private var accumulatedStart = 0.0
    private var current = CaptureFlags.paused
    private var stopping = false
    private var stoppedAt: Double?
    private var health = CaptionProgress(now: ProcessInfo.processInfo.systemUptime)
    private var watchdog: DispatchSourceTimer?
    private var model: String
    private let event: ([String: Any]) -> Void
    private let diagnosticURL: URL
    private let diagnosticSession: String
    private var nativeDropped = 0
    private var backendDropped = 0
    private var backlog = 0
    private var inferenceSeconds = 0.0
    private var gaps: [[String: Double]] = []
    private var extendRecoveryGap = false

    init(paths: RuntimePaths, lectureID: String, sessionID: String, model: String,
         event: @escaping ([String: Any]) -> Void) throws {
        self.event = event; self.model = model
        diagnosticSession = sessionID
        diagnosticURL = paths.logs.appendingPathComponent("live-health.json")
        if let data = try? Data(contentsOf: diagnosticURL), data.count < 100_000,
           let previous = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           previous["session_id"] as? String == sessionID,
           let saved = previous["gaps"] as? [[String: Double]] {
            gaps = Array(saved.suffix(64))
            extendRecoveryGap = previous["recovery_requested"] as? Bool == true
        }
        process = try paths.process(["live", "run", lectureID, sessionID, "--asr", model])
        process.standardInput = input; process.standardOutput = output
        // A dead inference child must never terminate the recording app via SIGPIPE.
        try protectInferencePipe(input.fileHandleForWriting)
        try FileManager.default.createDirectory(at: paths.logs, withIntermediateDirectories: true)
        // Model stderr is unrestricted third-party text. Numeric health diagnostics are separate.
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [weak self] child in
            self?.watchdog?.cancel()
            if child.terminationStatus != 0 {
                self?.event(["type": "error", "message": "实时字幕处理已停止，请稍后重新开启。录音开关不受影响。"])
            }
        }
        try process.run()
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .utility))
        timer.schedule(deadline: .now() + 5, repeating: 5)
        timer.setEventHandler { [weak self] in self?.checkProgress() }
        watchdog = timer; timer.resume()
        reads.async { [self] in
            var bytes = Data()
            while true {
                let chunk = output.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                bytes.append(chunk)
                if bytes.count > 1_000_000 {
                    event(["type": "error", "message": "实时字幕响应异常，已停止字幕处理。"])
                    process.terminate(); break
                }
                while let end = bytes.firstIndex(of: 10) {
                    let line = bytes.prefix(upTo: end)
                    bytes.removeSubrange(...end)
                    if let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                        observe(object)
                        if object["type"] as? String == "stopped" { persistHealth(recovering: false) }
                        event(object)
                    }
                }
            }
        }
    }

    private func observe(_ object: [String: Any]) {
        lock.lock(); defer { lock.unlock() }
        let now = ProcessInfo.processInfo.systemUptime
        if object["type"] as? String == "ready" { health.prepared(now: now) }
        if ["progress", "stopped"].contains(object["type"] as? String ?? ""),
           let end = object["processed_audio"] as? Double,
           let duration = object["inference_seconds"] as? Double {
            health.processed(end: end, duration: duration, now: now)
            inferenceSeconds = duration
            if let start = object["gap_start"] as? Double,
               let end = object["gap_end"] as? Double, start.isFinite, end.isFinite, end > start {
                gaps.append(["start": start, "end": end])
                gaps = Array(gaps.suffix(64))
            }
            backendDropped = object["dropped_packets"] as? Int ?? backendDropped
            backlog = object["backlog"] as? Int ?? backlog
            if object["type"] as? String == "stopped", health.inputEnd > health.processedEnd + 0.1 {
                gaps.append(["start": health.processedEnd, "end": health.inputEnd])
                gaps = Array(gaps.suffix(64))
            }
        }
    }

    private func checkProgress() {
        lock.lock()
        let now = ProcessInfo.processInfo.systemUptime
        let expired = stoppedAt.map { now - $0 > health.timeout } ?? health.stalled(now: now)
        lock.unlock()
        persistHealth(recovering: expired)
        if expired { abort() }
    }

    private func persistHealth(recovering: Bool) {
        lock.lock()
        let now = ProcessInfo.processInfo.systemUptime
        if recovering && health.inputEnd > health.processedEnd {
            let gap = ["start": health.processedEnd, "end": health.inputEnd]
            if gaps.last != gap { gaps.append(gap) }
        }
        let snapshot: [String: Any] = [
            "input_audio": health.inputEnd, "processed_audio": health.processedEnd,
            "no_progress_seconds": now - health.lastProgress, "timeout_seconds": health.timeout,
            "native_dropped": nativeDropped, "backend_dropped": backendDropped,
            "backlog": backlog, "inference_seconds": inferenceSeconds,
            "recovery_requested": recovering, "session_id": diagnosticSession,
            "gaps": Array(gaps.suffix(64))
        ]
        lock.unlock()
        if let data = try? JSONSerialization.data(withJSONObject: snapshot) {
            try? data.write(to: diagnosticURL, options: .atomic)
        }
    }

    func abort() {
        lock.lock(); stopping = true; lock.unlock()
        watchdog?.cancel()
        persistHealth(recovering: true)
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
    }

    // All accumulation is protected, even when a UI control and capture callback race.
    func mode(_ flags: CaptureFlags) {
        lock.lock(); defer { lock.unlock() }
        guard !stopping else { return }
        flushLocked()
        current = flags
        enqueueLocked(["type": "configure", "english": flags.english, "translate": flags.translate,
                       "asr": model], audio: false)
    }

    func selectModel(_ value: String) {
        lock.lock(); defer { lock.unlock() }
        guard !stopping, model != value else { return }
        flushLocked(); model = value
        enqueueLocked(["type": "configure", "english": current.english, "translate": current.translate,
                       "asr": model], audio: false)
    }

    func audio(_ data: Data, start: Double, flags: CaptureFlags) {
        lock.lock(); defer { lock.unlock() }
        guard !stopping else { return }
        if current != flags { flushLocked(); current = flags }
        if extendRecoveryGap, !gaps.isEmpty {
            gaps[gaps.count - 1]["end"] = max(gaps.last?["end"] ?? start, start)
            extendRecoveryGap = false
        }
        health.input(end: start + Double(data.count) / 64000)
        if accumulated.isEmpty { accumulatedStart = start }
        accumulated.append(data)
        while accumulated.count >= 16000 {
            let packet = Data(accumulated.prefix(16000))
            accumulated.removeFirst(16000)
            enqueueAudioLocked(packet, start: accumulatedStart)
            accumulatedStart += 0.25
        }
    }

    private func enqueueAudioLocked(_ data: Data, start: Double) {
        enqueueLocked(["type": "audio", "pcm": data.base64EncodedString(), "start": start,
                       "english": current.english, "translate": current.translate, "asr": model], audio: true)
    }
    private func flushLocked() {
        if !accumulated.isEmpty {
            enqueueAudioLocked(accumulated, start: accumulatedStart)
            accumulated.removeAll(keepingCapacity: true)
        }
    }
    private func enqueueLocked(_ object: [String: Any], audio: Bool) {
        if audio && pending >= 8 {
            nativeDropped += 1
            if nativeDropped % 40 == 1 { event(["type": "warning", "message": "字幕有短暂中断，可继续听课。"]) }
            return
        }
        guard var data = try? JSONSerialization.data(withJSONObject: object) else { return }
        data.append(10)
        pending += 1
        writes.async { [self] in
            defer { lock.lock(); pending -= 1; lock.unlock() }
            do { try input.fileHandleForWriting.write(contentsOf: data) }
            catch { event(["type": "error", "message": "实时字幕连接已中断，请重新开启字幕。"] ) }
        }
    }
    func stop() {
        lock.lock()
        guard !stopping else { lock.unlock(); return }
        flushLocked(); stopping = true
        stoppedAt = ProcessInfo.processInfo.systemUptime
        enqueueLocked(["type": "stop"], audio: false)
        lock.unlock()
        writes.async { [self] in try? input.fileHandleForWriting.close() }
    }
}

private final class LivePipeBox {
    private let lock = NSLock()
    private var pipe: LivePipe?
    var present: Bool { lock.lock(); defer { lock.unlock() }; return pipe != nil }
    func replace(_ new: LivePipe?) {
        lock.lock(); let old = pipe; pipe = new; lock.unlock()
        old?.stop()
    }
    func abort() {
        lock.lock(); let old = pipe; pipe = nil; lock.unlock()
        old?.abort()
    }
    func mode(_ flags: CaptureFlags) { lock.lock(); let value = pipe; lock.unlock(); value?.mode(flags) }
    func selectModel(_ model: String) { lock.lock(); let value = pipe; lock.unlock(); value?.selectModel(model) }
    func audio(_ bytes: Data, start: Double, flags: CaptureFlags) {
        lock.lock(); let value = pipe; lock.unlock(); value?.audio(bytes, start: start, flags: flags)
    }
}

@MainActor
final class LiveController: NSObject, ObservableObject {
    @Published private(set) var flags = CaptureFlags.paused
    private(set) var captions: [LiveCaption] = [] {
        didSet { displayCaptions = LiveCaption.paragraphs(captions) }
    }
    @Published private(set) var displayCaptions: [LiveCaption] = []
    @Published private(set) var message = "随时开始，随时暂停"
    @Published private(set) var lectureID: String?
    @Published private(set) var isCapturing = false
    @Published private(set) var level: Float = 0
    @Published private(set) var modelSelection = RecognitionSelection()
    var activeModel: String? { modelSelection.active }
    var pendingModel: String? { modelSelection.pending }
    @Published private(set) var recoveryExhausted = false
    @Published private(set) var pendingSaves = 0
    @Published var error: String?
    @Published var inputDevice: UInt32 = UInt32(UserDefaults.standard.integer(forKey: "inputDevice"))
    private var engine: AVAudioEngine?
    private var pump: CapturePump?
    private let pipes = LivePipeBox()
    private var generation = 0
    private var sessionID: String?
    private var referenceTime = ProcessInfo.processInfo.systemUptime
    private var hasTap = false
    private var historyGeneration = 0
    private var captureStarted = false
    private var captureID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    private var pipeToken: UUID?
    private var drainingTokens: [UUID: String] = [:]
    private var recoveryBudget = CaptionRecoveryBudget()
    private var recoveryTask: Task<Void, Never>?
    private var restartCaptions: (() -> Void)?
    var onLibraryChange: (() -> Void)?

    override init() {
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(shutdown),
                                               name: NSApplication.willTerminateNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(sleeping),
                                                          name: NSWorkspace.willSleepNotification, object: nil)
    }

    private func prepareEngine() -> AVAudioEngine {
        if let engine { return engine }
        let created = AVAudioEngine()
        engine = created
        NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange,
                                                object: created, queue: .main) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.flags.needsMicrophone, self.engine?.isRunning == false else { return }
                self.shutdown(); self.message = "音频设备已变化，已保存当前录音，请重新开启。"
            }
        }
        return created
    }

    func change(_ desired: CaptureFlags, lecture: Lecture, backend: Backend, model: String) {
        if flags.needsMicrophone && lectureID != lecture.id {
            error = "请先暂停当前课堂，再切换课堂。"; return
        }
        if !flags.needsMicrophone {
            recoveryBudget = CaptionRecoveryBudget(); recoveryExhausted = false
        }
        if desired.needsASR && !pipes.present { modelSelection.request(model) }
        recoveryTask?.cancel(); recoveryTask = nil
        restartCaptions = { [weak self] in
            guard let self else { return }
            self.change(self.flags, lecture: lecture, backend: backend, model: model)
        }
        flags = desired; generation += 1
        let revision = generation
        Task { [self] in
            if !desired.needsMicrophone { shutdown(); return }
            let granted: Bool
            switch AVCaptureDevice.authorizationStatus(for: .audio) {
            case .authorized: granted = true
            case .notDetermined: granted = await AVCaptureDevice.requestAccess(for: .audio)
            default: granted = false
            }
            guard revision == generation else { return }
            guard granted else {
                flags = .paused; message = "请在系统设置中允许 LectureFlow 使用麦克风。"
                error = message; return
            }
            do {
                if lectureID != lecture.id {
                    sessionID = nil; captions = []; lectureID = lecture.id
                    captureStarted = false
                }
                if !captureStarted {
                    recoveryBudget = CaptionRecoveryBudget()
                    captions = []
                    referenceTime = ProcessInfo.processInfo.systemUptime; captureStarted = true
                    captureID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
                    sessionID = nil
                }
                if pump == nil {
                    let new = CapturePump(audioRoot: URL(fileURLWithPath: lecture.path).appendingPathComponent("Audio"),
                                          referenceTime: referenceTime, captureID: captureID)
                    let box = pipes
                    new.onPCM = { data, start, flags in box.audio(data, start: start, flags: flags) }
                    new.onLevel = { [weak self] value in Task { @MainActor in self?.level = value } }
                    new.onMode = { flags in box.mode(flags) }
                    new.onRecording = { [weak self] directory in
                        Task { @MainActor in
                            self?.pendingSaves += 1
                            defer { self?.pendingSaves -= 1 }
                            do {
                                _ = try await backend.call(["library", "register-recording", lecture.id, directory.path])
                                self?.onLibraryChange?()
                            } catch { self?.error = "录音文件已保留，可用“恢复录音”重新加入课堂。" }
                        }
                    }
                    new.onDiscardedRecording = { [weak self] directory in
                        Task { @MainActor in
                            do {
                                _ = try await backend.call(["library", "discard-recording", lecture.id,
                                                            directory.path, "--confirmed"])
                                self?.onLibraryChange?()
                            } catch { self?.error = "录音已封存。取消未完成，可暂停其他处理后从片段列表移入废纸篓。" }
                        }
                    }
                    new.onFailure = { [weak self] message in
                        Task { @MainActor in self?.shutdown(); self?.error = message }
                    }
                    pump = new
                }
                pump?.update(desired)
                let engine = prepareEngine()
                if !engine.isRunning {
                    let node = engine.inputNode
                    try AudioDevices.select(inputDevice, for: node)
                    let format = node.outputFormat(forBus: 0)
                    guard format.sampleRate > 0 && format.channelCount > 0 else {
                        throw BackendError.unavailable("没有可用的麦克风。")
                    }
                    let captured = pump!
                    node.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, time in
                        let clock = time.isHostTimeValid ? AVAudioTime.seconds(forHostTime: time.hostTime)
                            : ProcessInfo.processInfo.systemUptime
                        captured.submit(buffer, hostTime: clock)
                    }
                    hasTap = true; engine.prepare(); try engine.start()
                }
                isCapturing = true
                if !desired.needsASR {
                    modelSelection.reset()
                    pipeToken = nil; pipes.replace(nil); message = "正在保存录音"; return
                }
                if !pipes.present {
                    message = "正在准备本地字幕，录音开关独立生效"
                    if sessionID == nil {
                        let created = try JSONDecoder().decode(Created.self, from: await backend.call([
                            "live", "create", lecture.id, "--id", captureID,
                            "--language", SourceLanguagePreference.load()
                        ])).id
                        guard revision == generation else { return }
                        sessionID = created
                    }
                    guard revision == generation else { return }
                    while !drainingTokens.isEmpty {
                        try await Task.sleep(for: .milliseconds(200))
                        guard revision == generation else { return }
                    }
                    let token = UUID(); pipeToken = token
                    let selectedModel = modelSelection.requested ?? model
                    let pipe = try LivePipe(paths: backend.paths, lectureID: lecture.id, sessionID: sessionID!, model: selectedModel) {
                        [weak self] event in
                        Task { @MainActor in self?.receive(event, token: token, sourceLecture: lecture.id) }
                    }
                    pipes.replace(pipe); pipes.mode(desired)
                } else { selectRecognitionModel(model) }
            } catch {
                if engine?.isRunning == true && flags.recording {
                    flags.english = false; flags.translate = false
                    pipes.replace(nil); pump?.update(flags)
                } else { shutdown() }
                self.error = error.localizedDescription
            }
        }
    }

    private func receive(_ event: [String: Any], token: UUID, sourceLecture: String) {
        let kind = event["type"] as? String
        if kind == "stopped" || (kind == "error" && drainingTokens[token] != nil) {
            drainingTokens.removeValue(forKey: token)
            onLibraryChange?()
            return
        }
        guard sourceLecture == lectureID else { return }
        if token != pipeToken && kind != "caption" { return }
        switch event["type"] as? String {
        case "progress": break
        case "ready":
            if let model = event["model"] as? String { modelSelection.acknowledge(model) }
            message = "实时字幕已就绪"
        case "loading": message = event["message"] as? String ?? "正在准备模型"
        case "model_pending":
            message = "将在当前语音片段结束后切换模型，录音继续保存"
        case "model_changed":
            if let model = event["model"] as? String { modelSelection.acknowledge(model) }
            message = "识别模型已切换，录音连续保存"
        case "warning": message = event["message"] as? String ?? "字幕有短暂中断"
        case "error":
            pipeToken = nil; pipes.abort()
            guard flags.needsASR, isCapturing else { return }
            guard let delay = recoveryBudget.nextDelay() else {
                recoveryExhausted = true
                flags.english = false; flags.translate = false
                if flags.recording { pump?.update(flags) } else { shutdown() }
                message = "字幕自动恢复已达上限，请暂停后重试；已有录音继续保存。"
                return
            }
            let expectedGeneration = generation
            message = "字幕处理中断，正在自动恢复；少量译文可能未完成，可用录音课后转写。"
            recoveryTask = Task { [weak self] in
                try? await Task.sleep(for: .seconds(delay))
                guard !Task.isCancelled, let self, self.generation == expectedGeneration,
                      self.flags.needsASR, self.isCapturing else { return }
                self.restartCaptions?()
            }
        case "caption":
            guard CaptionEventPolicy.accepts(
                token: token, activeToken: pipeToken, eventSession: event["session_id"] as? String,
                currentSession: sessionID, drainingSession: drainingTokens[token],
                captureStarted: captureStarted) else { return }
            guard let data = try? JSONSerialization.data(withJSONObject: event),
                  let caption = try? JSONDecoder().decode(LiveCaption.self, from: data) else { return }
            if let index = captions.firstIndex(where: { $0.id == caption.id }) {
                if captions[index].revision < caption.revision { captions[index] = caption }
            } else {
                captions.append(caption)
                if captions.count > 5000 { captions.removeFirst(captions.count - 5000) }
            }
        default: break
        }
    }

    func loadHistory(lecture: Lecture, backend: Backend) async {
        guard !flags.needsMicrophone, lectureID == lecture.id else { return }
        historyGeneration += 1
        let request = historyGeneration
        if lectureID != lecture.id {
            sessionID = nil; captureStarted = false; lectureID = lecture.id
        }
        do {
            let history = try JSONDecoder().decode(LiveHistory.self, from: await backend.call([
                "live", "history", lecture.id
            ]))
            if !Task.isCancelled && !flags.needsMicrophone && request == historyGeneration
                && lectureID == lecture.id { captions = Array(history.captions.suffix(5000)) }
        } catch { self.error = error.localizedDescription }
    }

    func prepareHistory(for id: String?) {
        guard !flags.needsMicrophone else { return }
        historyGeneration += 1
        captions = []
        if lectureID != id { sessionID = nil; captureStarted = false; lectureID = id }
    }

    private func drainCaptions() {
        recoveryTask?.cancel(); recoveryTask = nil
        if let token = pipeToken, let sessionID {
            drainingTokens[token] = sessionID
        }
        pipeToken = nil; pipes.replace(nil)
    }

    @objc func shutdown() {
        // Pause: stop mic/ASR but keep the open writer so continue stays one user segment.
        generation += 1
        engine?.stop()
        if hasTap { engine?.inputNode.removeTap(onBus: 0); hasTap = false }
        pump?.softPause()
        pump?.barrier()
        drainCaptions(); flags = .paused
        isCapturing = false; level = 0; modelSelection.reset()
        message = "已暂停；继续后仍属同一段录音"
    }
    func endSession() {
        generation += 1
        engine?.stop()
        if hasTap { engine?.inputNode.removeTap(onBus: 0); hasTap = false }
        pump?.sealRecording()
        pump?.barrier()
        pump = nil
        drainCaptions(); flags = .paused
        isCapturing = false; level = 0; modelSelection.reset()
        captureStarted = false; sessionID = nil
        message = "Lecture 已结束；可追加录制或生成课后转写"
    }
    func selectRecognitionModel(_ model: String) {
        guard !isCapturing else {
            error = "请先暂停，再切换识别模型。"
            return
        }
        guard flags.needsASR, modelSelection.requested != model else { return }
        modelSelection.request(model)
        message = activeModel == model ? "保持当前识别模型" : "将在下次开始时使用所选识别模型"
        if pipes.present { pipes.selectModel(model) }
    }
    func cancelCurrentRecording() {
        guard flags.recording else { return }
        generation += 1
        flags.recording = false
        pump?.cancelRecording()
        if !flags.needsMicrophone { shutdown() }
        else {
            message = "本段录音已取消，字幕继续运行；可从废纸篓恢复录音。"
            if !pipes.present && !recoveryExhausted { restartCaptions?() }
        }
    }

    @objc private func sleeping() {
        endSession()
        message = "电脑即将睡眠，已结束当前采集并封存录音。"
    }
}
