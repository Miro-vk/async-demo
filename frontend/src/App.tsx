import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Detail } from "./components/Detail";
import { Queue } from "./components/Queue";
import type { Counts, EmailDetail, InboxItem, Thresholds } from "./types";

const EMPTY: Counts = { total: 0, proceed: 0, review: 0, stop: 0, pending_review: 0, reviewed: 0 };

const FILTER_PARAMS: Record<string, { action?: string; pendingOnly?: boolean }> = {
  queue: { action: "review", pendingOnly: true },
  proceed: { action: "proceed" },
  stop: { action: "stop" },
  all: {},
};

export default function App() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [counts, setCounts] = useState<Counts>(EMPTY);
  const [filter, setFilter] = useState("queue");
  const [selected, setSelected] = useState(0);
  const [detail, setDetail] = useState<EmailDetail | null>(null);
  const [thresholds, setThresholds] = useState<Thresholds | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.thresholds().then(setThresholds).catch(() => undefined);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const refresh = useCallback(async () => {
    try {
      const data = await api.inbox(FILTER_PARAMS[filter] ?? {});
      setItems(data.items);
      setCounts(data.counts);
      setSelected(0);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [filter]);

  useEffect(() => { void refresh(); }, [refresh]);

  const open = useCallback(async (id: string) => {
    setDetail(await api.email(id));
  }, []);

  // Keyboard-first, because a triage queue that needs a mouse per item does not
  // get used on a Monday morning with sixty emails in it.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const tag = (event.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      if (detail) {
        if (event.key === "Escape") setDetail(null);
        return;
      }
      if (event.key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        setSelected((s) => Math.min(s + 1, items.length - 1));
      } else if (event.key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        setSelected((s) => Math.max(s - 1, 0));
      } else if (event.key === "Enter" && items[selected]) {
        void open(items[selected].email_id);
      } else if (["1", "2", "3", "4"].includes(event.key)) {
        setFilter(["queue", "proceed", "stop", "all"][Number(event.key) - 1]);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, selected, detail, open]);

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">§</span>
          <div>
            <strong>Intake triage</strong>
            <span>Vance &amp; Brock LLP</span>
          </div>
        </div>

        <div className="rail-note">
          <p>
            Synthetic corpus. Every decision below is reproducible from a seed, and
            nothing is ever sent to anyone.
          </p>
        </div>

        {thresholds && (
          <div className="rail-thresholds">
            <h4>Abstention thresholds</h4>
            <dl>
              <div><dt>Label</dt><dd>{thresholds.classification.toFixed(2)}</dd></div>
              <div><dt>Party name</dt><dd>{thresholds.party_name.toFixed(2)}</dd></div>
              <div><dt>Matter type</dt><dd>{thresholds.matter_type.toFixed(2)}</dd></div>
              <div><dt>Conflicts</dt><dd>any hit</dd></div>
            </dl>
            <p>Raising these sends more mail to a person.</p>
          </div>
        )}

        <button className="theme" onClick={() => setTheme(theme === "light" ? "dark" : "light")}>
          {theme === "light" ? "◐ Dark" : "◑ Light"}
        </button>
      </aside>

      <main className="main">
        {error && (
          <p className="warn api-error">
            {error} — is the API running? <code>make api</code>
          </p>
        )}
        {detail ? (
          <Detail
            detail={detail}
            thresholds={thresholds}
            onBack={() => { setDetail(null); void refresh(); }}
            onReviewed={(updated) => { setDetail(updated); void refresh(); }}
          />
        ) : (
          <Queue
            items={items}
            counts={counts}
            filter={filter}
            selected={selected}
            onFilter={setFilter}
            onSelect={setSelected}
            onOpen={open}
          />
        )}
      </main>
    </div>
  );
}
