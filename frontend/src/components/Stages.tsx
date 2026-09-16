import type { PipelineResult, Thresholds } from "../types";
import { Provenance } from "./primitives";

/** Four stages, deliberately not four identical green ticks.
 *
 *  Classify and extract are a model reading prose. Resolve and dispatch are rules
 *  over records. Those carry different risk, and flattening them into one visual
 *  treatment would tell a reviewer the wrong thing about where to look first. */

const STAGE_META: Record<string, { title: string; blurb: string }> = {
  classify: { title: "Classify", blurb: "What kind of email is this" },
  extract:  { title: "Extract",  blurb: "Facts, each with a source span" },
  resolve:  { title: "Resolve",  blurb: "Match records, check conflicts" },
  dispatch: { title: "Dispatch", blurb: "Route, draft, open a stub" },
};

export function Stages({ result, thresholds }: { result: PipelineResult; thresholds: Thresholds | null }) {
  return (
    <div className="stages">
      {result.traces.map((trace) => {
        const meta = STAGE_META[trace.stage] ?? { title: trace.stage, blurb: "" };
        const deterministic = trace.provider === "deterministic";
        return (
          <section
            key={trace.stage}
            className={`stage ${deterministic ? "stage-rules" : "stage-model"} ${trace.ok ? "" : "stage-bad"}`}
          >
            <header className="stage-head">
              <h3>{meta.title}</h3>
              <Provenance kind={deterministic ? "rules" : "model"} />
            </header>
            <p className="stage-blurb">{meta.blurb}</p>
            <dl className="stage-meta">
              <div><dt>Ran on</dt><dd>{deterministic ? trace.model : trace.model}</dd></div>
              {!deterministic && (
                <div><dt>Source</dt><dd>{trace.cached ? "cached response" : "live call"}</dd></div>
              )}
              {trace.output_tokens !== null && (
                <div><dt>Output</dt><dd>{trace.output_tokens} tok</dd></div>
              )}
            </dl>
            {!trace.ok && <p className="stage-error">{trace.failure_reason}</p>}
          </section>
        );
      })}
      {thresholds && (
        <section className="stage stage-policy">
          <header className="stage-head">
            <h3>Decide</h3>
            <Provenance kind="rules" />
          </header>
          <p className="stage-blurb">Act, or ask a human</p>
          <dl className="stage-meta">
            <div><dt>Label bar</dt><dd>{thresholds.classification.toFixed(2)}</dd></div>
            <div><dt>Party name bar</dt><dd>{thresholds.party_name.toFixed(2)}</dd></div>
            <div><dt>Conflicts</dt><dd>any hit at all</dd></div>
          </dl>
        </section>
      )}
    </div>
  );
}
