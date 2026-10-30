import AppKit
import AVFoundation
import SwiftUI

@MainActor
final class AudioPlayback: ObservableObject {
    @Published private(set) var segmentID: String?
    private var player: AVQueuePlayer?
    func play(_ segment: AudioSegment) {
        if segmentID == segment.id { stop(); return }
        stop()
        let items = segment.files.map { AVPlayerItem(url: URL(fileURLWithPath: $0)) }
        guard !items.isEmpty else { return }
        player = AVQueuePlayer(items: items)
        segmentID = segment.id
        player?.play()
    }
    func stop() { player?.pause(); player = nil; segmentID = nil }

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
