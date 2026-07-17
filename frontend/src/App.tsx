import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { ChamberDetail } from "./ChamberDetail";
import { stanceLabel } from "./format";
import type { Chamber } from "./types";

export function App() {
  const [chambers, setChambers] = useState<Chamber[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [topic, setTopic] = useState("");
  const [category, setCategory] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setChambers(await api.listChambers());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load chambers");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onCreate(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const chamber = await api.createChamber({ topic, category });
      setTopic("");
      setCategory("");
      await refresh();
      setSelectedId(chamber.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to create chamber");
    }
  }

  if (selectedId) {
    return (
      <div className="app">
        <ChamberDetail
          chamberId={selectedId}
          onBack={() => {
            setSelectedId(null);
            void refresh();
          }}
        />
      </div>
    );
  }

  return (
    <div className="app">
      <h1>Cicero — Debate Chambers</h1>
      {error && <p className="error">{error}</p>}

      <form className="card" onSubmit={onCreate}>
        <div className="row">
          <input
            aria-label="topic"
            placeholder="Debate topic / question"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            style={{ flex: 1, minWidth: 240 }}
            required
          />
          <input
            aria-label="category"
            placeholder="Category (optional)"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          />
          <button className="primary" type="submit">
            Create chamber
          </button>
        </div>
      </form>

      {chambers.length === 0 ? (
        <p className="muted">No chambers yet. Create one above.</p>
      ) : (
        chambers.map((c) => (
          <div className="card" key={c.id}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div>
                <strong>{c.topic}</strong>{" "}
                <span className="muted">· {c.status}</span>
                <div className="muted" style={{ fontSize: "0.85rem" }}>
                  {c.participants.length} participant(s):{" "}
                  {c.participants.map((p) => `${p.display_name} (${stanceLabel(p.stance)})`).join(", ") ||
                    "none yet"}
                </div>
              </div>
              <button onClick={() => setSelectedId(c.id)}>Open</button>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
