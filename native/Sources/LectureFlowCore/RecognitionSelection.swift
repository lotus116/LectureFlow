import Foundation

/// Requested and loaded models are distinct while the inference child starts or switches.
public struct RecognitionSelection: Equatable, Sendable {
    public private(set) var requested: String?
    public private(set) var active: String?
    public var pending: String? { requested == active ? nil : requested }
    public init() {}
    public mutating func request(_ id: String) { requested = id }
    public mutating func acknowledge(_ id: String) { active = id }
    public mutating func reset() { requested = nil; active = nil }
}

/// A missing preference migrates to high accuracy. Explicit choices survive restarts.
public enum RecognitionPreference {
    public static let key = "recognitionModel"
    public static func load(from defaults: UserDefaults = .standard) -> String {
        let saved = defaults.string(forKey: key)
        let model = ["asr-small", "asr-large"].contains(saved ?? "") ? saved! : "asr-large"
        defaults.set(model, forKey: key)
        return model
    }
    public static func save(_ model: String, to defaults: UserDefaults = .standard) {
        guard ["asr-small", "asr-large"].contains(model) else { return }
        defaults.set(model, forKey: key)
    }
}

/// Version one exposed unrestricted detection as "Chinese/English auto".
/// Its saved value cannot establish consent to the corrected experimental option.
public enum SourceLanguagePreference {
    public static let key = "sourceLanguageV2"
    public static func load(from defaults: UserDefaults = .standard) -> String {
        let saved = defaults.string(forKey: key)
        let language = saved == "auto" ? "auto" : "English"
        defaults.set(language, forKey: key)
        return language
    }
}
