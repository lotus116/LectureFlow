import AppKit
import AVFoundation
import SwiftUI

@MainActor
final class AudioPlayback: ObservableObject {
    @Published private(set) var segmentID: String?
    @Published private(set) var reviewID: String?
    @Published private(set) var error: String?
    private var player: AVQueuePlayer?
    private var finishedObserver: NSObjectProtocol?
    private var failureObservers: [NSObjectProtocol] = []
    func play(_ segment: AudioSegment) {
        if segmentID == segment.id { stop(); return }
        stop()
        let items = segment.files.map { AVPlayerItem(url: URL(fileURLWithPath: $0)) }
        guard !items.isEmpty else { return }
        player = AVQueuePlayer(items: items)
        segmentID = segment.id
        player?.play()
    }
    func playReview(_ audio: ReviewAudio, markID: String) {
        if reviewID == markID { stop(); return }
        stop(); error = nil
        guard !audio.files.isEmpty, audio.offset.isFinite, audio.offset >= 0,
              audio.files.allSatisfy({ FileManager.default.fileExists(atPath: $0) }) else {
            error = "对应原音不可用，文字与标记仍保留。"; return
        }
        let items = audio.files.map { AVPlayerItem(url: URL(fileURLWithPath: $0)) }
        let queue = AVQueuePlayer(items: items)
        player = queue; segmentID = audio.segment_id; reviewID = markID
        finishedObserver = NotificationCenter.default.addObserver(forName: .AVPlayerItemDidPlayToEndTime,
            object: items.last, queue: .main) { [weak self, weak queue] _ in
                Task { @MainActor in if let queue, self?.player === queue { self?.stop() } }
            }
        failureObservers = items.map { item in
            NotificationCenter.default.addObserver(forName: .AVPlayerItemFailedToPlayToEndTime,
                object: item, queue: .main) { [weak self, weak queue] _ in
                    Task { @MainActor in
                        guard let self, let queue, self.player === queue else { return }
                        self.stop(); self.error = "原音播放失败；标记与文字仍保留。"
                    }
                }
        }
        queue.seek(to: CMTime(seconds: audio.offset, preferredTimescale: 16000),
                   toleranceBefore: .zero, toleranceAfter: .zero) { [weak self, weak queue] finished in
            Task { @MainActor in
                guard let self, let queue, self.player === queue else { return }
                if finished { queue.play() }
                else { self.stop(); self.error = "原音定位未完成，请重试。" }
            }
        }
    }
    func stop() {
        player?.pause(); player = nil; segmentID = nil; reviewID = nil
        if let finishedObserver { NotificationCenter.default.removeObserver(finishedObserver) }
        finishedObserver = nil
        failureObservers.forEach { NotificationCenter.default.removeObserver($0) }
        failureObservers = []
    }

    func export(_ segment: AudioSegment) async throws {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.prompt = "导出到这里"
        guard panel.runModal() == .OK, let destination = panel.url else { return }
        let accessed = destination.startAccessingSecurityScopedResource()
        defer { if accessed { destination.stopAccessingSecurityScopedResource() } }
        let folder = destination.appendingPathComponent("LectureFlow-录音-\(segment.id.prefix(8))-\(UUID().uuidString.prefix(6))")
        let files = segment.files
        try await Task.detached(priority: .userInitiated) {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            for (index, file) in files.enumerated() {
                let source = URL(fileURLWithPath: file)
                let name = files.count == 1 ? segment.original_name
                    : String(format: "%03d-", index + 1) + source.lastPathComponent
                let target = folder.appendingPathComponent(URL(fileURLWithPath: name).lastPathComponent)
                try FileManager.default.copyItem(at: source, to: target)
            }
        }.value
        NSWorkspace.shared.open(folder)
    }
}
