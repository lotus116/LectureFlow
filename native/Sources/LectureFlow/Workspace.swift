import AppKit
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
    let backend = Backend()
    private var contentKey = ""

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
            if selectedLecture == nil {
                selectedLecture = overview.courses.first?.lectures.first?.id
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
            if needsWorker { try await backend.startWorker() }
        } catch { self.error = error.localizedDescription }
    }

    func perform(_ arguments: [String]) async {
        busy = true
        defer { busy = false }
        do {
            _ = try await backend.call(arguments)
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
