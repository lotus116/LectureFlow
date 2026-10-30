public struct CaptureFlags: Equatable, Sendable {
    public var recording: Bool
    public var english: Bool
    public var translate: Bool
    public init(recording: Bool = false, english: Bool = false, translate: Bool = false) {
        self.recording = recording; self.english = english; self.translate = translate
    }
    public var needsMicrophone: Bool { recording || english || translate }
    public var needsASR: Bool { english || translate }
    public static let all = CaptureFlags(recording: true, english: true, translate: true)
    public static let paused = CaptureFlags()
}
