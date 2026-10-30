public struct CaptureFlags: Equatable, Codable, Sendable {
    public var recording: Bool
    public var english: Bool
    public var translate: Bool
    public init(recording: Bool = false, english: Bool = false, translate: Bool = false) {
        self.recording = recording; self.english = english; self.translate = translate
    }
    public var needsMicrophone: Bool { recording || english || translate }
    public var needsASR: Bool { english || translate }
    public func available(asr: Bool, translation: Bool) -> CaptureFlags {
        CaptureFlags(recording: recording, english: english && asr,
                     translate: translate && asr && translation)
    }
    public static let all = CaptureFlags(recording: true, english: true, translate: true)
    public static let paused = CaptureFlags()
}
