export function pcmWav(samples: Float32Array): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index++) view.setUint8(offset + index, value.charCodeAt(index));
  };
  ascii(0, "RIFF"); view.setUint32(4, buffer.byteLength - 8, true);
  ascii(8, "WAVE"); ascii(12, "fmt "); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  ascii(36, "data"); view.setUint32(40, samples.length * 2, true);
  samples.forEach((value, index) => {
    const sample = Math.max(-1, Math.min(1, value));
    view.setInt16(44 + index * 2, sample < 0 ? sample * 32768 : sample * 32767, true);
  });
  return new Blob([buffer], { type: "audio/wav" });
}

export function joinDictation(left: string, right: string): string {
  if (!left) return right;
  if (!right) return left;
  return `${left}${/[a-z0-9]$/i.test(left) && /^[a-z0-9]/i.test(right) ? " " : ""}${right}`;
}

const phoneMicrophone = /iphone|continuity|接力|连续互通|连续性摄像头/i;
const builtInMicrophone = /built[ -]?in|internal|macbook|内置|内建/i;

async function microphones(): Promise<MediaDeviceInfo[]> {
  return (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "audioinput");
}

function preferredMicrophone(devices: MediaDeviceInfo[], exclude?: string) {
  const local = devices.filter((device) => device.deviceId && device.deviceId !== exclude
    && device.deviceId !== "default" && device.deviceId !== "communications"
    && device.label && !phoneMicrophone.test(device.label));
  return local.find((device) => builtInMicrophone.test(device.label)) ?? local[0];
}

export class PcmRecorder {
  private stream?: MediaStream;
  private context?: AudioContext;
  private source?: MediaStreamAudioSourceNode;
  private node?: AudioWorkletNode;
  private closed = false;
  private stopping = false;
  private recovering = false;
  private unwatchTrack?: () => void;
  private onEnded?: () => void;
  private onDeviceChange = () => { void this.checkDevice(); };

  private async capture(device?: MediaDeviceInfo) {
    return navigator.mediaDevices.getUserMedia({ audio: {
      // Keep microphone gain stable: automatic gain can raise distant office
      // speech during pauses. This is not target-speaker separation.
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false,
      ...(device ? { deviceId: { exact: device.deviceId } } : {}),
    } });
  }

  private attach(stream: MediaStream) {
    this.unwatchTrack?.(); this.source?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = stream;
    this.source = this.context!.createMediaStreamSource(stream);
    this.source.connect(this.node!);
    const track = stream.getAudioTracks()[0];
    const ended = () => { void this.recover(); };
    const muted = () => {
      // Continuity Camera can pause its track rather than end it on disconnect.
      if (phoneMicrophone.test(track.label)) void this.recover();
    };
    track.addEventListener("ended", ended);
    track.addEventListener("mute", muted);
    this.unwatchTrack = () => {
      track.removeEventListener("ended", ended);
      track.removeEventListener("mute", muted);
    };
    if (track.readyState === "ended") throw new Error("麦克风已断开连接");
  }

  private async checkDevice() {
    if (this.closed || this.stopping || this.recovering || !this.stream) return;
    const track = this.stream.getAudioTracks()[0];
    const id = track.getSettings().deviceId;
    try {
      const devices = await microphones();
      if (this.closed || this.stopping || track !== this.stream?.getAudioTracks()[0]) return;
      if (track.readyState === "ended" || (id && id !== "default" && id !== "communications"
        && !devices.some((device) => device.deviceId === id))) await this.recover();
    } catch { /* Some browsers cannot enumerate devices while the page is hidden. */ }
  }

  private async recover() {
    if (this.closed || this.stopping || this.recovering) return;
    this.recovering = true;
    try {
      const oldId = this.stream?.getAudioTracks()[0].getSettings().deviceId;
      const device = preferredMicrophone(await microphones(), oldId);
      if (this.closed || this.stopping) return;
      if (!device) throw new Error("No remaining microphone");
      const replacement = await this.capture(device);
      if (this.closed || this.stopping) { replacement.getTracks().forEach((track) => track.stop()); return; }
      this.attach(replacement);
    } catch {
      // Finish the existing draft when no other microphone is available.
      if (!this.closed && !this.stopping) {
        const onEnded = this.onEnded; this.onEnded = undefined; onEnded?.();
      }
    } finally { this.recovering = false; }
  }

  async start(onData: (samples: Float32Array) => void, onEnded?: () => void) {
    this.onEnded = onEnded;
    try {
      const preferred = preferredMicrophone(await microphones().catch(() => []));
      if (this.closed) return;
      try { this.stream = await this.capture(preferred); }
      catch (error) {
        // A device may disappear between enumeration and acquisition.
        if (!preferred || !(error instanceof DOMException)
          || !["NotFoundError", "OverconstrainedError"].includes(error.name)) throw error;
        const alternative = preferredMicrophone(await microphones());
        if (this.closed) return;
        this.stream = await this.capture(alternative);
      }
      if (this.closed) { this.release(); return; }
      // Labels may only become available after the first permission grant.
      if (phoneMicrophone.test(this.stream.getAudioTracks()[0].label)) {
        const local = preferredMicrophone(await microphones().catch(() => []));
        if (this.closed) { this.release(); return; }
        if (local) {
          this.stream.getTracks().forEach((track) => track.stop());
          this.stream = await this.capture(local);
          if (this.closed) { this.release(); return; }
        }
      }
      this.context = new AudioContext();
      await this.context.resume();
      if (!this.context.audioWorklet) throw new Error("当前浏览器不支持语音录音");
      await this.context.audioWorklet.addModule("/dictation-worklet.js");
      if (this.closed) { this.release(); return; }
      this.node = new AudioWorkletNode(this.context, "dictation-pcm");
      this.node.port.onmessage = (event: MessageEvent<unknown>) => {
        if (event.data instanceof Float32Array) onData(event.data);
      };
      const stream = this.stream; this.stream = undefined;
      this.attach(stream);
      this.node.connect(this.context.destination);
      navigator.mediaDevices.addEventListener("devicechange", this.onDeviceChange);
    } catch (error) {
      this.release();
      throw error;
    }
  }

  async stop() {
    this.stopping = true;
    if (this.node) {
      const node = this.node;
      await new Promise<void>((resolve) => {
        const timer = window.setTimeout(resolve, 200);
        const previous = node.port.onmessage;
        node.port.onmessage = (event: MessageEvent) => {
          if (event.data?.flushed) { window.clearTimeout(timer); resolve(); }
          else previous?.call(node.port, event);
        };
        node.port.postMessage("flush");
      });
    }
    this.release();
  }

  release() {
    this.closed = true;
    navigator.mediaDevices.removeEventListener("devicechange", this.onDeviceChange);
    this.unwatchTrack?.(); this.unwatchTrack = undefined; this.onEnded = undefined;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source?.disconnect(); this.node?.disconnect();
    if (this.node) this.node.port.onmessage = null;
    if (this.context?.state !== "closed") void this.context?.close().catch(() => undefined);
    this.node = undefined; this.context = undefined; this.stream = undefined; this.source = undefined;
  }
}

export function pcm16(samples: Float32Array): ArrayBuffer {
  const data = new ArrayBuffer(samples.length * 2);
  const view = new DataView(data);
  samples.forEach((sample, index) => {
    const value = Math.max(-1, Math.min(1, sample));
    view.setInt16(index * 2, value < 0 ? value * 32768 : value * 32767, true);
  });
  return data;
}
