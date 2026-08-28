import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  createPlaylist,
  importPlaylist,
  listPlaylists,
  type PlaylistSummary,
} from "../api/bot";

const CARD = "rounded-xl border border-border bg-bg-elev p-6";
const CARD_LABEL = "text-[10px] font-semibold uppercase tracking-[0.18em] text-dim";
const INPUT =
  "rounded-md border border-border bg-surface px-3 py-2 text-sm text-text outline-none transition-colors focus:border-accent placeholder:text-dim";
const BTN =
  "rounded-full px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40";
const BTN_PRIMARY = `${BTN} bg-accent text-black hover:bg-accent-hover`;
const BTN_GHOST = `${BTN} border border-border text-text hover:border-accent hover:text-accent`;

export default function PlaylistsPage() {
  const navigate = useNavigate();
  const [playlists, setPlaylists] = useState<PlaylistSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [importUrl, setImportUrl] = useState("");
  const [importName, setImportName] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listPlaylists()
      .then((res) => {
        setPlaylists(res.playlists);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    setFormError(null);
    try {
      await createPlaylist(name);
      setNewName("");
      refresh();
      navigate(`/playlists/${encodeURIComponent(name)}`);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleImport(e: React.FormEvent) {
    e.preventDefault();
    const url = importUrl.trim();
    if (!url) return;
    setBusy(true);
    setFormError(null);
    try {
      const res = await importPlaylist(url, importName.trim() || undefined);
      setImportUrl("");
      setImportName("");
      refresh();
      navigate(`/playlists/${encodeURIComponent(res.name)}`);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Library</h1>
        <p className="mt-1 text-sm text-muted">
          Saved playlists. Create an empty one and add tracks, or import a whole
          playlist/album from a URL.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <section className={CARD}>
          <h2 className={`${CARD_LABEL} mb-3`}>New playlist</h2>
          <form className="flex flex-wrap gap-2" onSubmit={handleCreate}>
            <input
              type="text"
              placeholder="Name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className={`${INPUT} min-w-[180px] flex-1`}
              required
            />
            <button type="submit" disabled={busy || !newName.trim()} className={BTN_PRIMARY}>
              Create
            </button>
          </form>
        </section>

        <section className={CARD}>
          <h2 className={`${CARD_LABEL} mb-3`}>Import from URL</h2>
          <form className="flex flex-wrap gap-2" onSubmit={handleImport}>
            <input
              type="text"
              placeholder="YouTube / Spotify / SoundCloud playlist URL"
              value={importUrl}
              onChange={(e) => setImportUrl(e.target.value)}
              className={`${INPUT} min-w-[200px] flex-1`}
              required
            />
            <input
              type="text"
              placeholder="(name)"
              value={importName}
              onChange={(e) => setImportName(e.target.value)}
              className={`${INPUT} min-w-[120px]`}
            />
            <button type="submit" disabled={busy || !importUrl.trim()} className={BTN_GHOST}>
              {busy ? "Importing…" : "Import"}
            </button>
          </form>
        </section>
      </div>

      {formError && <p className="text-sm text-danger">{formError}</p>}

      {playlists.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-bg-elev p-10 text-center">
          <p className="text-muted">No playlists yet.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {playlists.map((p) => (
            <Link
              key={p.name}
              to={`/playlists/${encodeURIComponent(p.name)}`}
              className="group rounded-xl border border-border bg-bg-elev p-5 transition-all hover:-translate-y-0.5 hover:border-accent hover:bg-surface"
            >
              <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-lg bg-gradient-to-br from-accent/30 to-surface text-2xl text-white/70">
                ♫
              </div>
              <div className="truncate text-lg font-semibold tracking-tight group-hover:text-accent">
                {p.name}
              </div>
              <div className="mt-1 text-xs text-muted">
                {p.count} track{p.count === 1 ? "" : "s"}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
