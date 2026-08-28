import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import SearchBox from "../components/SearchBox";
import {
  addToPlaylist,
  deletePlaylist,
  getPlaylist,
  playPlaylist,
  removeTrack,
  type PlaylistEntry,
} from "../api/bot";

const CARD = "rounded-xl border border-border bg-bg-elev p-6";
const CARD_LABEL = "text-[10px] font-semibold uppercase tracking-[0.18em] text-dim";
const BTN =
  "rounded-full px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40";
const BTN_PRIMARY = `${BTN} bg-accent text-black hover:bg-accent-hover`;
const BTN_GHOST = `${BTN} border border-border text-text hover:border-accent hover:text-accent`;
const BTN_DANGER = `${BTN} border border-danger/50 text-danger hover:bg-danger/10`;
const ICON_BTN =
  "flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface text-xs text-muted transition-colors hover:border-danger hover:text-danger disabled:cursor-not-allowed disabled:opacity-40";

export default function PlaylistDetailPage() {
  const { name = "" } = useParams();
  const navigate = useNavigate();

  const [entries, setEntries] = useState<PlaylistEntry[]>([]);
  const [addQuery, setAddQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  const refresh = useCallback(() => {
    getPlaylist(name)
      .then((res) => {
        setEntries(res.entries);
        setMissing(false);
      })
      .catch((err) => {
        setMissing(true);
        setMsg(err instanceof Error ? err.message : String(err));
      });
  }, [name]);

  useEffect(() => {
    refresh();
  }, [refresh]);

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

  if (missing) {
    return (
      <div className="space-y-4">
        <button
          onClick={() => navigate("/playlists")}
          className="text-sm text-muted transition-colors hover:text-accent"
        >
          ‹ Library
        </button>
        <p className="text-muted">No playlist named “{name}”.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <button
        onClick={() => navigate("/playlists")}
        className="text-sm text-muted transition-colors hover:text-accent"
      >
        ‹ Library
      </button>

      {/* Header */}
      <section className="overflow-hidden rounded-2xl border border-border bg-gradient-to-br from-bg-elev to-surface p-8 shadow-xl">
        <div className="flex flex-col gap-6 md:flex-row md:items-end">
          <div className="flex h-40 w-40 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-accent/40 via-surface to-bg-elev text-5xl text-white/30 shadow-2xl">
            ♫
          </div>
          <div className="min-w-0 flex-1 space-y-3">
            <div className={CARD_LABEL}>Playlist</div>
            <h1 className="truncate text-4xl font-bold tracking-tight">{name}</h1>
            <p className="text-sm text-muted">
              {entries.length} track{entries.length === 1 ? "" : "s"}
            </p>

            <div className="flex flex-wrap items-center gap-2 pt-2">
              <button
                disabled={busy || entries.length === 0}
                onClick={() =>
                  run(
                    () => playPlaylist(name, false),
                    (r) => `Queued ${r.queued} tracks.`,
                  )
                }
                className={BTN_PRIMARY}
              >
                ▶ Play all
              </button>
              <button
                disabled={busy || entries.length === 0}
                onClick={() =>
                  run(
                    () => playPlaylist(name, true),
                    (r) => `Shuffled ${r.queued} tracks into the queue.`,
                  )
                }
                className={BTN_GHOST}
              >
                ⇄ Shuffle play
              </button>
              <button
                disabled={busy}
                onClick={() => {
                  if (confirm(`Delete playlist "${name}"?`)) {
                    run(() => deletePlaylist(name)).then(() => navigate("/playlists"));
                  }
                }}
                className={BTN_DANGER}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Add a track */}
      <section className={CARD}>
        <h2 className={`${CARD_LABEL} mb-3`}>Add a track</h2>
        <SearchBox
          value={addQuery}
          onChange={setAddQuery}
          onPick={(value) =>
            run(
              () => addToPlaylist(name, value),
              (r) => `Added ${r.title}.`,
            )
          }
          disabled={busy}
          placeholder="Song name or URL"
          buttonLabel="Add"
        />
      </section>

      {msg && (
        <p className="text-sm text-muted">
          <span className="text-accent">›</span> {msg}
        </p>
      )}

      {/* Tracks */}
      <section className={CARD}>
        <h2 className={`${CARD_LABEL} mb-3`}>Tracks</h2>
        {entries.length === 0 ? (
          <p className="text-sm text-muted">Empty.</p>
        ) : (
          <ol className="divide-y divide-border">
            {entries.map((e, i) => (
              <li
                key={i}
                className="flex items-center gap-3 py-2 text-sm transition-colors hover:bg-surface/40"
              >
                <span className="w-6 shrink-0 text-right text-xs text-dim">{i + 1}</span>
                <span className="flex-1 truncate">{e.title}</span>
                <button
                  disabled={busy}
                  onClick={() => run(() => removeTrack(name, i + 1))}
                  className={ICON_BTN}
                  title="Remove"
                >
                  ✕
                </button>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
