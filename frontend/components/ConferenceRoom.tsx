"use client";

import { useEffect, useRef, useState } from "react";
import JoinScreen from "@/components/JoinScreen";
import ParticipantBox from "@/components/ParticipantBox";
import TermSheetPanel from "@/components/TermSheetPanel";
import ControlBar from "@/components/ControlBar";
import { connectMeet, type MeetSocket } from "@/lib/meetSocket";
import { startCapture, type AudioCapture } from "@/lib/audioCapture";
import { useMeetStore } from "@/lib/store/meetStore";

export default function ConferenceRoom() {
  const [code, setCode] = useState<string | null>(null);
  // True from the moment "Done" is clicked until the server's "floor" event
  // confirms the lock flipped - blocks a second click while the turn (agent
  // call + TTS) is still being processed.
  const [processing, setProcessing] = useState(false);
  const socketRef = useRef<MeetSocket | null>(null);
  const captureRef = useRef<AudioCapture | null>(null);

  const { status, errorText, me, other, sheet, log, muted, speaking, turnHolder, floorOpen, applyServerEvent, setMuted, reset } =
    useMeetStore();

  useEffect(() => {
    setProcessing(false);
  }, [turnHolder]);

  const leave = async () => {
    socketRef.current?.close();
    socketRef.current = null;
    await captureRef.current?.stop();
    captureRef.current = null;
    reset();
    setCode(null);
  };

  const join = async (roomCode: string, name: string, lang: string) => {
    setCode(roomCode);
    useMeetStore.getState().setStatus("connecting");

    const socket = connectMeet(roomCode, name, lang, applyServerEvent, () => {
      useMeetStore.getState().setStatus("closed");
    });
    socketRef.current = socket;

    try {
      // Mic is captured continuously once granted, but chunks are only ever
      // forwarded while it's genuinely this party's open floor - the
      // push-to-talk gate lives here, not in when the mic itself runs.
      captureRef.current = await startCapture((chunk) => {
        const s = useMeetStore.getState();
        if (!s.muted && s.me && s.turnHolder === s.me.party_id && s.floorOpen) {
          socket.sendAudio(chunk);
        }
      });
    } catch {
      useMeetStore.getState().setError("Microphone access is required for a translated call.");
    }
  };

  const isMyTurn = !!me && turnHolder === me.party_id;

  const talkStart = () => {
    if (!isMyTurn || floorOpen || processing) return;
    socketRef.current?.sendControl("talk_start");
  };

  const talkDone = () => {
    if (!isMyTurn || !floorOpen) return;
    setProcessing(true);
    socketRef.current?.sendControl("talk_done");
  };

  const toggleMute = () => setMuted(!muted);

  if (!code) {
    return <JoinScreen onReady={join} />;
  }

  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100 flex flex-col">
      <header className="flex items-center gap-3 px-6 py-4 border-b border-neutral-800 shrink-0">
        <div className="h-9 w-9 rounded-lg bg-blue-600 flex items-center justify-center font-semibold">N</div>
        <div>
          <h1 className="text-sm font-semibold leading-tight">NyayBandhan Meet</h1>
          <p className="text-xs text-neutral-500">
            {status === "connecting" && "Connecting…"}
            {status === "connected" && "Live"}
            {status === "closed" && "Disconnected"}
            {status === "error" && (errorText || "Error")}
          </p>
        </div>
      </header>

      <div className="flex-1 min-h-0 p-4 flex flex-col-reverse lg:flex-row gap-4">
        <div className="flex flex-row lg:flex-col gap-3 w-full lg:w-64 shrink-0">
          <ParticipantBox party={me} placeholder="You" speaking={me ? speaking[me.party_id] : false} muted={muted} />
          <ParticipantBox party={other} placeholder="Waiting for the other person…" speaking={other ? speaking[other.party_id] : false} />
        </div>

        <TermSheetPanel sheet={sheet} log={log} className="flex-1 min-h-[50vh]" />
      </div>

      <ControlBar
        code={code}
        muted={muted}
        onToggleMute={toggleMute}
        onLeave={leave}
        isMyTurn={isMyTurn}
        floorOpen={floorOpen}
        processing={processing}
        otherPresent={!!other}
        otherName={other?.name ?? null}
        onTalkStart={talkStart}
        onTalkDone={talkDone}
      />
    </main>
  );
}
