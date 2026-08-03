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

  async function onDelete(id: string) {
    if (!window.confirm("Delete this chamber and its transcript? This cannot be undone.")) {
      return;
    }
    setError(null);
    try {
      await api.deleteChamber(id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to delete chamber");
    }
  }

  if (selectedId) {
    return (
      <main className="app">
        <ChamberDetail
          key={selectedId} // remount on switch (e.g. after clone) to reset stream state
          chamberId={selectedId}
          onBack={() => {
            setSelectedId(null);
            void refresh();
          }}
          onOpenChamber={setSelectedId}
        />
      </main>
    );
  }

  return (
    <main className="app">
      <h1>Cicero — Debate Chambers</h1>
      {error && <p className="error">{error}</p>}

      <form className="card" onSubmit={onCreate}>
        <div className="row">
          <input
            aria-label="topic"
            aria-describedby="topic-hint"
            placeholder="Motion — e.g. “Model weights should be published openly”"
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
        {/* Debaters take a pro/con stance on the motion, so a topic with no
            side to take (an either/or question) leaves the stance record
            meaningless. Advice, not validation — plenty of good motions are
            phrased as questions. */}
        <p id="topic-hint" className="muted" style={{ fontSize: "0.85rem", margin: "0.5rem 0 0" }}>
          Phrase the topic as something debaters can agree or disagree with. An
          either/or question (“do X, or do Y?”) gives them no side to take, so
          their recorded stances will not mean much.
        </p>
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
              <div className="row">
                <button onClick={() => setSelectedId(c.id)}>Open</button>
                <button
                  className="danger"
                  aria-label={`delete chamber ${c.topic}`}
                  onClick={() => void onDelete(c.id)}
                >
                  Delete
                </button>
              </div>
            </div>
          </div>
        ))
      )}
    </main>
  );
}
