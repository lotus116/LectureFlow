/// A result belongs to both an immutable entity identity and the current selection epoch.
/// A → B → A still rejects the response issued before the intermediate selection.
public struct ResultFence: Sendable {
    public struct Ticket: Sendable {
        let epoch: UInt64
        let owner: String
        let version: String
    }
    private var epoch: UInt64 = 0
    public init() {}
    public mutating func invalidate() { epoch &+= 1 }
    public func ticket(owner: String, version: String) -> Ticket {
        Ticket(epoch: epoch, owner: owner, version: version)
    }
    public func accepts(_ ticket: Ticket, owner: String?, version: String?) -> Bool {
        ticket.epoch == epoch && ticket.owner == owner && ticket.version == version
    }
}
