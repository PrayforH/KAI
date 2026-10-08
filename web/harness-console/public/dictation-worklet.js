// Capture mono PCM at 16 kHz. Weighted averaging keeps fractional resampling
// state across audio render blocks (including 44.1 kHz input).
class DictationProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.weight = 0;
    this.sum = 0;
    this.buffer = new Float32Array(1600);
    this.offset = 0;
    this.port.onmessage = () => {
      this.flush();
      this.port.postMessage({ flushed: true });
    };
  }
  flush() {
    if (this.offset) {
      const buffer = this.buffer.slice(0, this.offset);
      this.port.postMessage(buffer, [buffer.buffer]);
      this.offset = 0;
    }
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const sample of channel) {
      let remaining = 1;
      while (remaining > 1e-8) {
        const take = Math.min(remaining, this.ratio - this.weight);
        this.sum += sample * take;
        this.weight += take;
        remaining -= take;
        if (this.weight >= this.ratio - 1e-8) {
          this.buffer[this.offset++] = this.sum / this.ratio;
          this.weight = 0;
          this.sum = 0;
          if (this.offset === this.buffer.length) this.flush();
        }
      }
    }
    return true;
  }
}
registerProcessor("dictation-pcm", DictationProcessor);
