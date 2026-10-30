import AudioToolbox
import AVFoundation
import CoreAudio
import Foundation

public struct InputDevice: Identifiable, Sendable {
    public let id: AudioDeviceID
    public let name: String
}

public enum AudioDevices {
    public static func inputs() -> [InputDevice] {
        var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address,
                                             0, nil, &size) == noErr else { return [] }
        var devices = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address,
                                        0, nil, &size, &devices) == noErr else { return [] }
        return devices.compactMap { device in
            var streams = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams,
                mScope: kAudioDevicePropertyScopeInput, mElement: kAudioObjectPropertyElementMain)
            var streamSize: UInt32 = 0
            guard AudioObjectGetPropertyDataSize(device, &streams, 0, nil, &streamSize) == noErr,
                  streamSize > 0 else { return nil }
            var property = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName,
                mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            var name: Unmanaged<CFString>?
            var nameSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            guard AudioObjectGetPropertyData(device, &property, 0, nil, &nameSize, &name) == noErr,
                  let name else { return nil }
            return InputDevice(id: device, name: name.takeRetainedValue() as String)
        }
    }

    public static func select(_ id: AudioDeviceID, for node: AVAudioInputNode) throws {
        var selected = id
        if selected == 0 {
            var property = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultInputDevice,
                mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            var size = UInt32(MemoryLayout<AudioDeviceID>.size)
            guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &property,
                0, nil, &size, &selected) == noErr else { throw CocoaError(.featureUnsupported) }
        }
        guard inputs().contains(where: { $0.id == selected }), let unit = node.audioUnit else {
            throw NSError(domain: "LectureFlow.Audio", code: -1,
                          userInfo: [NSLocalizedDescriptionKey: "选定的麦克风已断开，请重新选择。"])
        }
        let status = AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice,
                                         kAudioUnitScope_Global, 0, &selected,
                                         UInt32(MemoryLayout<AudioDeviceID>.size))
        if status != noErr {
            throw NSError(domain: "LectureFlow.Audio", code: Int(status),
                          userInfo: [NSLocalizedDescriptionKey: "无法使用选定的麦克风，请切换设备后重试。"])
        }
    }
}
