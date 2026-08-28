import { useCallback, useEffect, useRef, useState } from "react";
import { useStatus } from "../state/status";
import SearchBox from "../components/SearchBox";
import Visualizer from "../components/Visualizer";
import {
  getNowPlaying,
  moveQueueTrack,
  playbackControl,
  queuePlaylistUrl,
  queueTrack,
  removeQueueTrack,
  seek,
  setVolume,
  type NowPlaying,
  type PlaybackAction,
} from "../api/bot";

function fmtTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "0:00";
  const safe = Math.max(0, seconds);
  const m = Math.floor(safe / 60);
  const s = Math.floor(safe % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const CARD = "rounded-xl border border-border bg-bg-elev p-6";
const CARD_LABEL = "text-[10px] font-semibold uppercase tracking-[0.18em] text-dim";
const BADGE =
  "inline-flex items-center rounded-full border border-border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-muted";
const INPUT =
  "rounded-md border border-border bg-surface px-3 py-2 text-sm text-text outline-none transition-colors focus:border-accent placeholder:text-dim";
const BTN =
  "rounded-full px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40";
const BTN_PRIMARY = `${BTN} bg-accent text-black hover:bg-accent-hover`;
const BTN_GHOST = `${BTN} border border-border text-text hover:border-accent hover:text-accent`;
const BTN_DANGER = `${BTN} border border-danger/50 text-danger hover:bg-danger/10`;

export default function DashboardPage() {
  const { status } = useStatus();
  const [np, setNp] = useState<NowPlaying | null>(null);
  const [query, setQuery] = useState("");
  const [plUrl, setPlUrl] = useState("");
  const [vol, setVol] = useState(100);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // Smoothly interpolate `elapsed` between polls so the timer ticks.
  const baseElapsedRef = useRef<number>(0);
  const baseTsRef = useRef<number>(performance.now());
  const lastUrlRef = useRef<string | null>(null);
  const volDirtyRef = useRef<boolean>(false);
  const [displayElapsed, setDisplayElapsed] = useState<number>(0);

  const refresh = useCallback(() => {
    getNowPlaying()
      .then((data) => {
        setNp(data);
        if (!volDirtyRef.current) setVol(data.volume);
      })
      .catch(() => setNp(null));
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [refresh]);

  // Re-anchor the elapsed interpolation whenever a snapshot arrives.
  useEffect(() => {
    const url = np?.current?.url ?? np?.current?.title ?? null;
    const elapsed = typeof np?.elapsed === "number" ? np.elapsed : 0;
    if (url !== lastUrlRef.current) lastUrlRef.current = url;
    baseElapsedRef.current = elapsed;
    baseTsRef.current = performance.now();
    setDisplayElapsed(elapsed);
  }, [np]);

  // Tick while playing.
  useEffect(() => {
    if (!np?.current || np.paused || typeof np.elapsed !== "number") return;
    const id = setInterval(() => {
      const delta = (performance.now() - baseTsRef.current) / 1000;
      setDisplayElapsed(baseElapsedRef.current + delta);
    }, 250);
    return () => clearInterval(id);
  }, [np?.current, np?.paused, np?.elapsed]);

  async function run<T>(fn: () => Promise<T>, ok?: (r: T) => string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      if (ok) setMsg(ok(r));
      refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const control = (action: PlaybackAction) => run(() => playbackControl(action));

  async function submitQuery(value: string) {
    if (!value.trim()) return;
    await run(
      () => queueTrack(value.trim()),
      (res) => {
        const noun = `track${res.queued === 1 ? "" : "s"}`;
        return res.connected
          ? `Queued ${res.queued} ${noun}.`
          : `Queued ${res.queued} ${noun}. Start a call to begin playback.`;
      },
    );
  }

  async function submitPlaylist(e: React.FormEvent) {
    e.preventDefault();
    const url = plUrl.trim();
    if (!url) return;
    await run(
      () => queuePlaylistUrl(url),
      (res) => `Queued ${res.queued} track${res.queued === 1 ? "" : "s"} from playlist.`,
    );
    setPlUrl("");
  }

  function commitVolume(v: number) {
    volDirtyRef.current = true;
    run(() => setVolume(v)).finally(() => {
      volDirtyRef.current = false;
    });
  }

  async function editQueue(op: "up" | "down" | "remove", i: number) {
    // API positions are 1-based.
    if (op === "remove") return run(() => removeQueueTrack(i + 1));
    const to = op === "up" ? i : i + 2;
    return run(() => moveQueueTrack(i + 1, to));
  }

  const connected = !!np?.connected;
  const queueCount = (np?.queue.length ?? 0) + (np?.current ? 1 : 0);

  return (
    <div className="space-y-6">
      {/* Hero now-playing */}
      <section className="overflow-hidden rounded-2xl border border-border bg-gradient-to-br from-bg-elev to-surface p-8 shadow-xl">
        <div className="flex flex-col gap-6 md:flex-row md:items-end">
          <div className="flex h-48 w-48 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-gradient-to-br from-accent/30 via-surface to-bg-elev text-6xl text-white/20 shadow-2xl">
            ♫
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <div className={CARD_LABEL}>
              {connected ? "Streaming into the call" : "Not in a call"}
            </div>
            <div className="flex items-center gap-3">
              <h1
                className={`min-w-0 flex-1 truncate text-4xl font-bold tracking-tight ${
                  np?.current ? "" : "text-muted"
                }`}
              >
                {np?.current ? np.current.title : "Nothing playing"}
              </h1>
              <Visualizer active={connected && !np?.paused && !!np?.current} />
            </div>
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
              {np?.current && <span className={BADGE}>{np.current.source}</span>}
              {np?.current?.duration ? <span>{fmtTime(np.current.duration)}</span> : null}
              {np?.current && <span>{fmtTime(displayElapsed)}</span>}
              {np?.paused && <span className="text-accent">paused</span>}
            </div>

            <div className="mt-2 flex flex-wrap gap-2">
              <button
                disabled={busy || !np?.current}
                onClick={() => seek(-15).then(refresh)}
                className={BTN_GHOST}
                title="Rewind 15s"
              >
                ⏪ 15s
              </button>
              <button
                disabled={busy}
                onClick={() => control(np?.paused ? "resume" : "pause")}
                className={BTN_PRIMARY}
              >
                {np?.paused ? "▶ Resume" : "⏸ Pause"}
              </button>
              <button
                disabled={busy || !np?.current}
                onClick={() => seek(15).then(refresh)}
                className={BTN_GHOST}
                title="Forward 15s"
              >
                15s ⏩
              </button>
              <button
                disabled={busy}
                onClick={() => control("skip")}
                className={BTN_GHOST}
                title="Skip"
              >
                ⏭ Skip
              </button>
              <button
                disabled={busy}
                onClick={() => control("stop")}
                className={BTN_DANGER}
              >
                ⏹ Stop
              </button>
            </div>

            {/* Volume */}
            <div className="mt-2 flex items-center gap-3">
              <span className="text-xs text-dim">🔊</span>
              <input
                type="range"
                min={0}
                max={200}
                step={5}
                value={vol}
                onChange={(e) => setVol(Number(e.target.value))}
                onMouseUp={(e) => commitVolume(Number((e.target as HTMLInputElement).value))}
                onTouchEnd={(e) => commitVolume(Number((e.target as HTMLInputElement).value))}
                className="h-1 w-48 cursor-pointer accent-[var(--color-accent)]"
              />
              <span className="w-10 text-xs tabular-nums text-muted">{vol}%</span>
            </div>
          </div>
        </div>
      </section>

      {!status?.configured && (
        <div className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
          No <code className="rounded bg-surface px-1.5 py-0.5 text-xs">VOICE_ROOM_ID</code>{" "}
          is configured, so playback can't be controlled from here.
        </div>
      )}

      {/* Add to queue */}
      <section className={CARD}>
        <h2 className={`${CARD_LABEL} mb-3`}>Add to queue</h2>
        <SearchBox
          value={query}
          onChange={setQuery}
          onPick={submitQuery}
          disabled={busy}
          placeholder="Search, or paste a YouTube / Spotify / SoundCloud URL"
          buttonLabel="Queue"
        />
        <form className="mt-3 flex flex-wrap gap-2" onSubmit={submitPlaylist}>
          <input
            type="text"
            value={plUrl}
            onChange={(e) => setPlUrl(e.target.value)}
            placeholder="…or a playlist/album URL to queue the whole thing"
            className={`${INPUT} min-w-[260px] flex-1`}
          />
          <button type="submit" disabled={busy || !plUrl.trim()} className={BTN_GHOST}>
            Queue playlist
          </button>
        </form>
        {msg && (
          <p className="mt-3 text-sm text-muted">
            <span className="text-accent">›</span> {msg}
          </p>
        )}
      </section>

      {/* Queue */}
      <section className={CARD}>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className={CARD_LABEL}>Queue</h2>
          <span className="text-xs text-dim">
            {queueCount} track{queueCount === 1 ? "" : "s"}
          </span>
        </div>
        {np?.current || (np && np.queue.length > 0) ? (
          <ol className="divide-y divide-border">
            {np.current && (
              <li className="flex items-center gap-3 rounded-md bg-accent/10 px-2 py-2 text-sm">
                <span className="w-6 shrink-0 text-right text-accent" aria-label="Now playing">
                  ▶
                </span>
                <span className="flex-1 truncate font-semibold text-text">
                  {np.current.title}
                </span>
                <span className="text-[10px] uppercase tracking-wider text-accent">
                  now playing
                </span>
                <span className={BADGE}>{np.current.source}</span>
              </li>
            )}
            {np.queue.map((t, i) => (
              <li
                key={i}
                className="group flex items-center gap-3 py-2 text-sm transition-colors hover:bg-surface/40"
              >
                <span className="w-6 shrink-0 text-right text-xs text-dim">{i + 1}</span>
                <span className="flex-1 truncate">{t.title}</span>
                <span className={BADGE}>{t.source}</span>
                <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                  <button
                    disabled={busy || i === 0}
                    onClick={() => editQueue("up", i)}
                    title="Move up"
                    className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-surface text-xs text-muted transition-colors hover:border-accent hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    ↑
                  </button>
                  <button
                    disabled={busy || i === np.queue.length - 1}
                    onClick={() => editQueue("down", i)}
                    title="Move down"
                    className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-surface text-xs text-muted transition-colors hover:border-accent hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    ↓
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => editQueue("remove", i)}
                    title="Remove"
                    className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-surface text-xs text-muted transition-colors hover:border-danger hover:text-danger disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    ✕
                  </button>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-muted">Queue is empty.</p>
        )}
      </section>
    </div>
  );
}
