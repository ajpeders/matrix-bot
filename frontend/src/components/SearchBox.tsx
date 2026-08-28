import { useEffect, useId, useRef, useState } from "react";
import { searchSongs, type SearchResultItem } from "../api/bot";

const URL_RE = /^https?:\/\//i;

const INPUT =
  "rounded-md border border-border bg-surface px-3 py-2 text-sm text-text outline-none transition-colors focus:border-accent placeholder:text-dim";
const BTN =
  "rounded-full px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40";
const BTN_PRIMARY = `${BTN} bg-accent text-black hover:bg-accent-hover`;

interface Props {
  value: string;
  onChange: (v: string) => void;
  onPick: (query: string) => void;
  placeholder?: string;
  buttonLabel?: string;
  disabled?: boolean;
}

export default function SearchBox({
  value,
  onChange,
  onPick,
  placeholder,
  buttonLabel = "Queue",
  disabled = false,
}: Props) {
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(-1);
  const [loading, setLoading] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const trimmed = value.trim();
  const isUrl = URL_RE.test(trimmed);

  // Debounced search — skipped for URLs (those are queued verbatim).
  useEffect(() => {
    if (!trimmed || trimmed.length < 2 || isUrl) {
      setResults([]);
      setOpen(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    const id = setTimeout(async () => {
      try {
        const res = await searchSongs(trimmed, 5);
        setResults(res.results);
        setOpen(res.results.length > 0);
        setHi(-1);
      } catch {
        setResults([]);
        setOpen(false);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => clearTimeout(id);
  }, [trimmed, isUrl]);

  // Close on outside click
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, []);

  function commit(picked?: SearchResultItem) {
    if (picked) {
      onPick(picked.url);
    } else if (trimmed) {
      onPick(trimmed);
    }
    setOpen(false);
    onChange("");
    setResults([]);
    setHi(-1);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" && open) {
      e.preventDefault();
      setHi((h) => Math.min(h + 1, results.length - 1));
    } else if (e.key === "ArrowUp" && open) {
      e.preventDefault();
      setHi((h) => Math.max(h - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      commit(hi >= 0 ? results[hi] : undefined);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div ref={wrapRef} className="relative flex-1">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          commit(hi >= 0 ? results[hi] : undefined);
        }}
        className="flex items-center gap-2"
      >
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder={placeholder}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listId}
          className={`${INPUT} min-w-[260px] flex-1`}
        />
        <button type="submit" disabled={disabled || !trimmed} className={BTN_PRIMARY}>
          {buttonLabel}
        </button>
      </form>

      {open && results.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute left-0 right-0 top-full z-20 mt-2 max-h-96 overflow-y-auto rounded-xl border border-border bg-bg-elev shadow-2xl"
        >
          {results.map((r, i) => (
            <li key={r.url} role="option" aria-selected={i === hi}>
              <button
                type="button"
                onMouseEnter={() => setHi(i)}
                onMouseDown={(e) => {
                  // mousedown beats the input's blur, so the click commits.
                  e.preventDefault();
                  commit(r);
                }}
                className={`flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors ${
                  i === hi ? "bg-surface" : "hover:bg-surface/60"
                }`}
              >
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded bg-surface text-base text-dim">
                  ♫
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-text">{r.title}</div>
                </div>
                <span className="text-[10px] uppercase tracking-wider text-dim">
                  youtube
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {loading && open === false && trimmed.length >= 2 && !isUrl && (
        <div className="absolute left-0 right-0 top-full z-10 mt-2 rounded-md border border-border bg-bg-elev px-3 py-2 text-xs text-muted">
          Searching…
        </div>
      )}
    </div>
  );
}
