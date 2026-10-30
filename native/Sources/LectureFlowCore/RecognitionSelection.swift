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
