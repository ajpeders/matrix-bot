import { afterEach, describe, expect, it, vi } from "vitest";
import { getStatus, queueTrack, seek } from "./bot";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(body: unknown, ok = true, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);
}

describe("bot api", () => {
  it("fetches status", async () => {
    const fetchMock = mockFetch({ bot: "@bot:example.com", configured: true });
    const res = await getStatus();
    expect(res.bot).toBe("@bot:example.com");
    expect(fetchMock).toHaveBeenCalledWith("/api/status", expect.objectContaining({}));
  });

  it("posts a queue request with the query body", async () => {
    const fetchMock = mockFetch({ queued: 1, connected: false, tracks: [] });
    await queueTrack("test song");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/queue");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ query: "test song" });
  });

  it("posts a signed delta when seeking", async () => {
    const fetchMock = mockFetch({ ok: true, position: 30 });
    await seek(-15);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/seek");
    expect(JSON.parse(init?.body as string)).toEqual({ delta: -15 });
  });
});
