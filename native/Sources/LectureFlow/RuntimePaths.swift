import Foundation

/// Development and relocatable app bundles use the same backend protocol.
struct RuntimePaths: Sendable {
    let storage: URL
    let library: URL
    let models: URL
    let python: URL
    let bundledModels: URL?
    let previousModels: URL?
    var logs: URL { storage.appendingPathComponent("Logs") }

    init() {
        let env = ProcessInfo.processInfo.environment
        let resources = Bundle.main.resourceURL ?? Bundle.main.bundleURL
        let packaged = resources.appendingPathComponent("runtime/bin/python3.12")
        if FileManager.default.isExecutableFile(atPath: packaged.path) {
            let support = FileManager.default.urls(for: .applicationSupportDirectory,
                                                   in: .userDomainMask)[0]
            storage = URL(fileURLWithPath: env["LECTUREFLOW_STORAGE_ROOT"]
                          ?? support.appendingPathComponent("LectureFlow").path)
            python = packaged
            let bundled = resources.appendingPathComponent("Models")
            bundledModels = FileManager.default.fileExists(atPath: bundled.path) ? bundled : nil
            models = storage.appendingPathComponent("Models")
            let checkout = Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent()
            previousModels = FileManager.default.fileExists(atPath: checkout.appendingPathComponent("src/lectureflow/models.py").path)
                ? checkout.appendingPathComponent("models") : nil
            let previous = checkout.appendingPathComponent("data/library")
            let existing = FileManager.default.fileExists(atPath: checkout.appendingPathComponent("src/lectureflow/library.py").path)
                && FileManager.default.fileExists(atPath: previous.appendingPathComponent("Library.sqlite3").path)
            if env["LECTUREFLOW_LIBRARY_ROOT"] == nil,
               UserDefaults.standard.string(forKey: "libraryPath") == nil, existing {
                UserDefaults.standard.set(previous.path, forKey: "libraryPath")
            }
            library = URL(fileURLWithPath: env["LECTUREFLOW_LIBRARY_ROOT"]
                          ?? UserDefaults.standard.string(forKey: "libraryPath")
                          ?? storage.appendingPathComponent("Library").path)
        } else {
            let project = URL(fileURLWithPath: env["LECTUREFLOW_PROJECT_ROOT"]
                              ?? Bundle.main.object(forInfoDictionaryKey: "LectureFlowProjectRoot") as? String
                              ?? FileManager.default.currentDirectoryPath)
            storage = project.appendingPathComponent("data")
            python = project.appendingPathComponent(".venv/bin/python")
            bundledModels = nil
            previousModels = nil
            models = project.appendingPathComponent("models")
            library = URL(fileURLWithPath: env["LECTUREFLOW_LIBRARY_ROOT"]
                          ?? UserDefaults.standard.string(forKey: "libraryPath")
                          ?? project.appendingPathComponent("data/library").path)
        }
    }

    func process(_ arguments: [String]) throws -> Process {
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw BackendError.unavailable("本地运行组件缺失，请重新安装 LectureFlow 应用。")
        }
        try FileManager.default.createDirectory(at: storage, withIntermediateDirectories: true)
        let child = Process()
        child.executableURL = python
        child.currentDirectoryURL = storage
        child.arguments = ["-m", "lectureflow.cli", "--model-root", models.path,
                           "--library-root", library.path] + arguments
        child.standardInput = FileHandle.nullDevice
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"
        // A packaged runtime must not accidentally import a shell's Python packages.
        environment.removeValue(forKey: "PYTHONPATH")
        environment.removeValue(forKey: "PYTHONHOME")
        if let bundledModels { environment["LECTUREFLOW_BUNDLED_MODELS"] = bundledModels.path }
        child.environment = environment
        return child
    }

    func log(_ name: String) throws -> FileHandle {
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let file = logs.appendingPathComponent(name + ".log")
        if !FileManager.default.fileExists(atPath: file.path) {
            FileManager.default.createFile(atPath: file.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: file)
        let size = try handle.seekToEnd()
        if size > 2_000_000 { try handle.truncate(atOffset: 0) }
        return handle
    }
}
