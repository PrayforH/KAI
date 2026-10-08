import { afterEach, expect, test, vi } from "vitest";
import { PcmRecorder } from "../src/lib/dictation-audio";

class Track extends EventTarget {
  readyState = "live";
  stop = vi.fn(() => { this.readyState = "ended"; });
  constructor(public label: string, private id: string) { super(); }
  getSettings() { return { deviceId: this.id }; }
  end() { this.readyState = "ended"; this.dispatchEvent(new Event("ended")); }
}
function stream(track: Track) {
  return { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
}
function device(deviceId: string, label: string) {
  return { deviceId, label, kind: "audioinput" } as MediaDeviceInfo;
}
const builtin = device("mac", "MacBook Pro Microphone (Built-in)");
const phone = device("phone", "iPhone Microphone");
const headset = device("usb", "USB Headset");

function environment(initial: MediaDeviceInfo[], acquire?: (constraints: MediaStreamConstraints) => Promise<MediaStream>) {
  let devices = initial;
  const captures: Track[] = [];
  const media = Object.assign(new EventTarget(), {
    enumerateDevices: vi.fn(async () => devices),
    getUserMedia: vi.fn(acquire ?? (async (constraints: MediaStreamConstraints) => {
      const id = ((constraints.audio as MediaTrackConstraints).deviceId as ConstrainDOMStringParameters)?.exact ?? devices[0]?.deviceId;
      const selected = devices.find((entry) => entry.deviceId === id)!;
      const track = new Track(selected.label, selected.deviceId); captures.push(track);
      return stream(track);
    })),
  });
  const sources: { connect: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }[] = [];
  const close = vi.fn(async () => undefined);
  vi.stubGlobal("navigator", { mediaDevices: media });
  vi.stubGlobal("AudioContext", class {
    state = "running"; destination = {}; close = close;
    audioWorklet = { addModule: vi.fn(async () => undefined) };
    async resume() {}
    createMediaStreamSource() {
      const source = { connect: vi.fn(), disconnect: vi.fn() }; sources.push(source); return source;
    }
  });
  vi.stubGlobal("AudioWorkletNode", class {
    port = { onmessage: null }; connect() {} disconnect() {}
  });
  return { media, captures, sources, close, setDevices: (next: MediaDeviceInfo[]) => { devices = next; } };
}
afterEach(() => vi.unstubAllGlobals());

test("explicitly acquires the built-in microphone even when iPhone is the default", async () => {
  const env = environment([device("default", "Default - iPhone Microphone"), phone, headset, builtin]);
  const recorder = new PcmRecorder(); await recorder.start(vi.fn());
  expect(env.media.getUserMedia).toHaveBeenCalledExactlyOnceWith({ audio: {
    channelCount: 1, echoCancellation: true, noiseSuppression: true, deviceId: { exact: "mac" },
  } });
  recorder.release(); expect(env.captures[0].stop).toHaveBeenCalled();
});

test("selects a non-phone headset when the computer has no built-in microphone", async () => {
  const env = environment([phone, headset]);
  const recorder = new PcmRecorder(); await recorder.start(vi.fn());
  expect(env.captures[0].label).toBe("USB Headset"); recorder.release();
});

test("rechecks device labels after the first microphone permission grant", async () => {
  const phoneTrack = new Track(phone.label, phone.deviceId); const macTrack = new Track(builtin.label, builtin.deviceId);
  let calls = 0;
  const env = environment([device("", "")], async () => {
    env.setDevices([phone, builtin]); return stream(++calls === 1 ? phoneTrack : macTrack);
  });
  const recorder = new PcmRecorder(); await recorder.start(vi.fn());
  expect(env.media.getUserMedia).toHaveBeenCalledTimes(2);
  expect(env.media.getUserMedia.mock.calls[1][0]).toMatchObject({ audio: { deviceId: { exact: "mac" } } });
  expect(phoneTrack.stop).toHaveBeenCalledOnce(); recorder.release(); expect(macTrack.stop).toHaveBeenCalledOnce();
});

test("a disconnected microphone is replaced in the existing audio graph", async () => {
  const env = environment([builtin, headset]); const ended = vi.fn();
  const recorder = new PcmRecorder(); await recorder.start(vi.fn(), ended);
  env.setDevices([headset]); env.captures[0].end();
  await vi.waitFor(() => expect(env.sources).toHaveLength(2));
  expect(env.sources[0].disconnect).toHaveBeenCalled(); expect(env.sources[1].connect).toHaveBeenCalled();
  expect(env.captures[1].label).toBe(headset.label); expect(ended).not.toHaveBeenCalled();
  recorder.release();
});

test("device removal recovers even when the browser does not emit track ended", async () => {
  const env = environment([builtin, headset]); const recorder = new PcmRecorder(); await recorder.start(vi.fn());
  env.setDevices([headset]); env.media.dispatchEvent(new Event("devicechange"));
  await vi.waitFor(() => expect(env.sources).toHaveLength(2));
  recorder.release();
});

test("a paused Continuity microphone switches to a newly available local microphone", async () => {
  const env = environment([phone]); const recorder = new PcmRecorder(); await recorder.start(vi.fn());
  env.setDevices([phone, builtin]); env.captures[0].dispatchEvent(new Event("mute"));
  await vi.waitFor(() => expect(env.sources).toHaveLength(2));
  expect(env.captures[1].label).toBe(builtin.label); recorder.release();
});

test("without an alternative microphone, ends the session instead of capturing a dead default", async () => {
  const env = environment([builtin]); const ended = vi.fn();
  const recorder = new PcmRecorder(); await recorder.start(vi.fn(), ended);
  env.setDevices([]); env.captures[0].end();
  await vi.waitFor(() => expect(ended).toHaveBeenCalledOnce());
  expect(env.media.getUserMedia).toHaveBeenCalledOnce(); recorder.release();
  env.media.dispatchEvent(new Event("devicechange")); expect(ended).toHaveBeenCalledOnce();
});

test("cancelling while a replacement is being acquired stops the late stream", async () => {
  const first = new Track(builtin.label, builtin.deviceId); const replacement = new Track(headset.label, headset.deviceId);
  let resolve!: (value: MediaStream) => void; let calls = 0;
  const env = environment([builtin], async () => ++calls === 1 ? stream(first) : new Promise((done) => { resolve = done; }));
  const ended = vi.fn(); const recorder = new PcmRecorder(); await recorder.start(vi.fn(), ended);
  env.setDevices([headset]); first.end();
  await vi.waitFor(() => expect(env.media.getUserMedia).toHaveBeenCalledTimes(2));
  recorder.release(); resolve(stream(replacement));
  await vi.waitFor(() => expect(replacement.stop).toHaveBeenCalledOnce());
  expect(env.sources).toHaveLength(1); expect(ended).not.toHaveBeenCalled();
});

test("cancelling the permission prompt releases a stream granted later", async () => {
  let resolve!: (value: MediaStream) => void;
  const env = environment([builtin], () => new Promise((done) => { resolve = done; }));
  const recorder = new PcmRecorder(); const starting = recorder.start(vi.fn());
  await vi.waitFor(() => expect(env.media.getUserMedia).toHaveBeenCalledOnce());
  recorder.release(); const late = new Track(builtin.label, builtin.deviceId); resolve(stream(late));
  await starting; expect(late.stop).toHaveBeenCalledOnce(); expect(env.sources).toHaveLength(0);
});
