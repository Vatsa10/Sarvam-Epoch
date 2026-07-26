// Ports static/index.html's startCapture() into TypeScript for the Next.js meet UI.
// Mic -> AudioWorklet (public/pcm-worklet.js) -> 16kHz mono PCM16 Int16Array chunks,
// handed to `onChunk` roughly every ~100ms. stop() flushes the worklet's trailing
// partial buffer first so the last word of an utterance isn't dropped.
export type AudioCapture = {
  stop: () => Promise<void>;
  setMuted: (muted: boolean) => void;
};

export async function startCapture(onChunk: (chunk: Int16Array) => void): Promise<AudioCapture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const ctx = new AudioContext();
  // next.config.mjs sets basePath: "/meet", so public/pcm-worklet.js is served
  // at /meet/pcm-worklet.js (both in the static export and in `next dev`) -
  // not at the site root.
  await ctx.audioWorklet.addModule("/meet/pcm-worklet.js");
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-worklet");

  let muted = false;
  node.port.onmessage = (e: MessageEvent<Int16Array>) => {
    if (!muted) onChunk(e.data);
  };
  src.connect(node);

  return {
    setMuted(next: boolean) {
      muted = next;
    },
    async stop() {
      node.port.postMessage({ flush: true });
      await new Promise((r) => setTimeout(r, 30));
      node.port.onmessage = null;
      node.disconnect();
      src.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      await ctx.close();
    },
  };
}
