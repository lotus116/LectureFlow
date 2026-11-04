import AppKit
import LectureFlowCore

@main struct QuitProbe {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = LectureFlowDelegate()
        let workspace = Workspace()
        delegate.workspace = workspace
        app.delegate = delegate
        let ledger = workspace.live.exitReadiness
        let recording = ledger.begin(.recording)
        let captions = ledger.begin(.captions)
        let mode = CommandLine.arguments.last ?? "wait"
        func mark(_ text: String) { print(text); fflush(stdout) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { app.terminate(nil) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
            mark("waiting-before-recording")
            if mode == "repeat" { app.terminate(nil) }
            ledger.finish(recording, failure: mode == "failure" ? "Synthetic registration failure" : nil)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) {
            mark("waiting-before-captions")
            if mode == "cancel" { delegate.cancelTermination() }
            ledger.finish(captions)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.3) {
            if mode == "failure" {
                mark("failure-held-exit")
                delegate.acknowledgeIncompleteTermination()
            } else if mode == "cancel" {
                guard !workspace.isExiting, !workspace.live.isCapturing else { exit(2) }
                mark("cancel-kept-app-with-mic-off")
                app.terminate(nil)
            } else { mark("ERROR-did-not-exit"); exit(3) }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) { mark("ERROR-timeout"); exit(4) }
        withExtendedLifetime((workspace, delegate)) { app.run() }
    }
}
