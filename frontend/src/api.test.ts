import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  addNote,
  addParticipant,
  cloneChamber,
  createChamber,
  getServerConfig,
  eventsUrl,
  exportUrl,
  listChambers,
  listModels,
  removeParticipant,
  resumeDebate,
  startDebate,
  stepDebate,
  updateChamber,
  updateParticipant,
  updateSettings,
} from "./api";

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: "x",
    json: async () => body,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("api client", () => {
  it("listChambers GETs /chambers and returns the array", async () => {
    const fetchMock = mockFetch(200, [{ id: "1" }]);
    vi.stubGlobal("fetch", fetchMock);
    const result = await listChambers();
    expect(fetchMock).toHaveBeenCalledWith("/chambers", expect.any(Object));
    expect(result).toEqual([{ id: "1" }]);
  });

  it("createChamber POSTs the body as JSON", async () => {
    const fetchMock = mockFetch(200, { id: "abc" });
    vi.stubGlobal("fetch", fetchMock);
    await createChamber({ topic: "Mars?" });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ topic: "Mars?" });
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("addParticipant hits the participants sub-resource", async () => {
    const fetchMock = mockFetch(200, { id: "c1" });
    vi.stubGlobal("fetch", fetchMock);
    await addParticipant("c1", {
      display_name: "Ada",
      provider: "mock",
      model: "m",
      stance: "pro",
    });
    expect(fetchMock.mock.calls[0][0]).toBe("/chambers/c1/participants");
  });

  it("updateSettings PUTs to the settings sub-resource", async () => {
    const fetchMock = mockFetch(200, { id: "c1" });
    vi.stubGlobal("fetch", fetchMock);
    await updateSettings("c1", { max_rounds: 3, decision_rule: "majority" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/chambers/c1/settings");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ max_rounds: 3, decision_rule: "majority" });
  });

  it("updateChamber PATCHes only the fields it is given", async () => {
    const fetchMock = mockFetch(200, { id: "c1", topic: "Venus?" });
    vi.stubGlobal("fetch", fetchMock);
    const chamber = await updateChamber("c1", { topic: "Venus?" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/chambers/c1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ topic: "Venus?" });
    expect(chamber.topic).toBe("Venus?");
  });

  it("updateParticipant PATCHes the participant sub-resource", async () => {
    const fetchMock = mockFetch(200, { id: "c1" });
    vi.stubGlobal("fetch", fetchMock);
    await updateParticipant("c1", "p9", { model: "mock-large", stance: "con" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/chambers/c1/participants/p9");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ model: "mock-large", stance: "con" });
  });

  it("removeParticipant DELETEs and returns the updated chamber", async () => {
    const fetchMock = mockFetch(200, { id: "c1", participants: [] });
    vi.stubGlobal("fetch", fetchMock);
    const chamber = await removeParticipant("c1", "p9");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/chambers/c1/participants/p9");
    expect(init.method).toBe("DELETE");
    expect(chamber.participants).toEqual([]);
  });

  it("addNote POSTs the note content", async () => {
    const fetchMock = mockFetch(202, { status: "queued" });
    vi.stubGlobal("fetch", fetchMock);
    const res = await addNote("c1", "Focus on costs.");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/chambers/c1/notes");
    expect(JSON.parse(init.body)).toEqual({ content: "Focus on costs." });
    expect(res.status).toBe("queued");
  });

  it("getServerConfig GETs /config", async () => {
    const fetchMock = mockFetch(200, { web_access_enabled: true });
    vi.stubGlobal("fetch", fetchMock);
    const config = await getServerConfig();
    expect(fetchMock.mock.calls[0][0]).toBe("/config");
    expect(config.web_access_enabled).toBe(true);
  });

  it("cloneChamber POSTs to /clone and returns the new chamber", async () => {
    const fetchMock = mockFetch(201, { id: "new-id", status: "draft" });
    vi.stubGlobal("fetch", fetchMock);
    const clone = await cloneChamber("c1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/chambers/c1/clone");
    expect(init.method).toBe("POST");
    expect(clone.id).toBe("new-id");
  });

  it("startDebate POSTs to /run", async () => {
    const fetchMock = mockFetch(202, { status: "running" });
    vi.stubGlobal("fetch", fetchMock);
    const res = await startDebate("c1");
    expect(fetchMock.mock.calls[0][0]).toBe("/chambers/c1/run");
    expect(res.status).toBe("running");
  });

  it("resumeDebate POSTs to /resume", async () => {
    const fetchMock = mockFetch(202, { status: "running" });
    vi.stubGlobal("fetch", fetchMock);
    const res = await resumeDebate("c1");
    expect(fetchMock.mock.calls[0][0]).toBe("/chambers/c1/resume");
    expect(res.status).toBe("running");
  });

  it("stepDebate POSTs to /step and returns the updated chamber", async () => {
    const fetchMock = mockFetch(200, { id: "c1", status: "paused", turns: [{ id: "t1" }] });
    vi.stubGlobal("fetch", fetchMock);
    const chamber = await stepDebate("c1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/chambers/c1/step");
    expect(init.method).toBe("POST");
    expect(chamber.status).toBe("paused");
    expect(chamber.turns).toHaveLength(1);
  });

  it("throws ApiError with the server detail on non-2xx", async () => {
    const fetchMock = mockFetch(409, { detail: "only a draft chamber can be run" });
    vi.stubGlobal("fetch", fetchMock);
    await expect(startDebate("c1")).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
      message: "only a draft chamber can be run",
    });
  });

  it("ApiError carries the status code", () => {
    const err = new ApiError(404, "nope");
    expect(err.status).toBe(404);
    expect(err).toBeInstanceOf(Error);
  });

  it("builds events and export URLs", () => {
    expect(eventsUrl("c1")).toBe("/chambers/c1/events");
    expect(exportUrl("c1", "markdown")).toBe("/chambers/c1/export?format=markdown");
  });

  it("listModels GETs the provider's models and unwraps the list", async () => {
    const fetchMock = mockFetch(200, { provider: "ollama", models: ["llama3", "mistral"] });
    vi.stubGlobal("fetch", fetchMock);
    const models = await listModels("ollama");
    expect(fetchMock.mock.calls[0][0]).toBe("/providers/ollama/models");
    expect(models).toEqual(["llama3", "mistral"]);
  });

  it("listModels surfaces a 502 as ApiError", async () => {
    const fetchMock = mockFetch(502, { detail: "Ollama request failed: ConnectError" });
    vi.stubGlobal("fetch", fetchMock);
    await expect(listModels("ollama")).rejects.toMatchObject({
      name: "ApiError",
      status: 502,
    });
  });
});
