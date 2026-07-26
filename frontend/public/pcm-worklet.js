// frontend/public/pcm-worklet.js
// Downsamples the browser's native rate to 16kHz mono and emits Int16 PCM frames.
// The STT WebSocket accepts wav/pcm only - webm/opus from MediaRecorder is rejected.
// Ported verbatim from static/pcm-worklet.js (the push-to-talk demo's proven capture
// path) - Next's static export serves anything under public/ at the site root, so
// this loads at /pcm-worklet.js exactly like the vanilla-JS UI's copy does.
class PCMWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.target = 16000;
    this.ratio = sampleRate / this.target;   // sampleRate is a worklet global
    this.pos = 0;
    this.buf = [];
    this.port.onmessage = e => {
      if (e.data && e.data.flush && this.buf.length) {
        this.port.postMessage(Int16Array.from(this.buf));
        this.buf = [];
      }
    };
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    // linear decimation - adequate for speech, and cheap
    for (let i = 0; i < ch.length; i++) {
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const s = Math.max(-1, Math.min(1, ch[i]));
        this.buf.push(s < 0 ? s * 0x8000 : s * 0x7fff);
      }
    }
    if (this.buf.length >= 1600) {          // ~100ms at 16kHz
      this.port.postMessage(Int16Array.from(this.buf));
      this.buf = [];
    }
    return true;
  }
}
registerProcessor('pcm-worklet', PCMWorklet);
