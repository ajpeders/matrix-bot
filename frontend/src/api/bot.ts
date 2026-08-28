import { apiFetch } from "./client";

// The bot is single-voice-room (VOICE_ROOM_ID), so — unlike the Discord sibling —
// there is no per-guild dimension; every endpoint acts on the one voice room.

export interface StatusResponse {
  bot: string | null;
  voice_room: string | null;
  configured: boolean;
  connected: boolean;
  now_playing: string | null;
  queue_length: number;
  paused: boolean;
}

export interface TrackInfo {
  title: string;
  url: string | null;
  source: "youtube" | "local";
  duration: number | null;
  uploader: string | null;
  thumbnail: string | null;
  requester: string | null;
}

export interface NowPlaying {
  configured: boolean;
  connected: boolean;
  paused: boolean;
  volume: number;
  current: TrackInfo | null;
  queue: TrackInfo[];
  elapsed: number | null;
  duration: number | null;
}

export interface PlaylistSummary {
  name: string;
  count: number;
}

export interface PlaylistEntry {
  title: string;
  source?: string;
  url?: string | null;
}

export interface SearchResultItem {
  title: string;
  url: string;
  source: string;
  duration: number | null;
  uploader: string | null;
  thumbnail: string | null;
}

export type PlaybackAction = "pause" | "resume" | "skip" | "stop" | "shuffle";

export const getStatus = () => apiFetch<StatusResponse>("/api/status");

export const getNowPlaying = () => apiFetch<NowPlaying>("/api/now-playing");

export const searchSongs = (q: string, limit = 5) =>
  apiFetch<{ results: SearchResultItem[] }>(
    `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  );

export const queueTrack = (query: string) =>
  apiFetch<{ queued: number; connected: boolean; tracks: TrackInfo[] }>("/api/queue", {
    method: "POST",
    body: JSON.stringify({ query }),
  });

export const queuePlaylistUrl = (url: string, limit?: number) =>
  apiFetch<{ queued: number; connected: boolean }>("/api/queue/playlist", {
    method: "POST",
    body: JSON.stringify({ url, limit }),
  });

export const removeQueueTrack = (index: number) =>
  apiFetch<{ removed: TrackInfo }>(`/api/queue/${index}`, { method: "DELETE" });

export const moveQueueTrack = (from: number, to: number) =>
  apiFetch<{ ok: boolean }>("/api/queue/move", {
    method: "POST",
    body: JSON.stringify({ from, to }),
  });

export const playbackControl = (action: PlaybackAction) =>
  apiFetch<{ ok: boolean }>(`/api/playback/${action}`, { method: "POST" });

export const seek = (delta: number) =>
  apiFetch<{ ok: boolean; position: number }>("/api/seek", {
    method: "POST",
    body: JSON.stringify({ delta }),
  });

export const setVolume = (volume: number) =>
  apiFetch<{ volume: number }>("/api/volume", {
    method: "POST",
    body: JSON.stringify({ volume }),
  });

export const listPlaylists = () =>
  apiFetch<{ playlists: PlaylistSummary[] }>("/api/playlists");

export const createPlaylist = (name: string) =>
  apiFetch<{ ok: boolean }>("/api/playlists", {
    method: "POST",
    body: JSON.stringify({ name }),
  });

export const importPlaylist = (url: string, name?: string, limit?: number) =>
  apiFetch<{ name: string; imported: number }>("/api/playlists/import", {
    method: "POST",
    body: JSON.stringify({ url, name, limit }),
  });

export const getPlaylist = (name: string) =>
  apiFetch<{ name: string; entries: PlaylistEntry[] }>(
    `/api/playlists/${encodeURIComponent(name)}`,
  );

export const addToPlaylist = (name: string, query: string) =>
  apiFetch<{ title: string; position: number }>(
    `/api/playlists/${encodeURIComponent(name)}`,
    { method: "POST", body: JSON.stringify({ query }) },
  );

export const deletePlaylist = (name: string) =>
  apiFetch<{ ok: boolean }>(`/api/playlists/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });

export const removeTrack = (name: string, index: number) =>
  apiFetch<{ removed: PlaylistEntry }>(
    `/api/playlists/${encodeURIComponent(name)}/tracks/${index}`,
    { method: "DELETE" },
  );

export const playPlaylist = (name: string, shuffle = false) =>
  apiFetch<{ queued: number; connected: boolean }>(
    `/api/playlists/${encodeURIComponent(name)}/play`,
    { method: "POST", body: JSON.stringify({ shuffle }) },
  );
