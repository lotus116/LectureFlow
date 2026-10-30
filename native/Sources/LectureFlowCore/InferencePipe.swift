import Darwin
import Foundation

public func protectInferencePipe(_ handle: FileHandle) throws {
    guard fcntl(handle.fileDescriptor, F_SETNOSIGPIPE, 1) != -1 else {
        throw CocoaError(.fileWriteUnknown)
    }
}
