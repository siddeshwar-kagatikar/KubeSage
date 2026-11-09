import { Link } from 'react-router-dom'
import { useLlmCalls, useLlmOps } from '../api/hooks'
import type { LatestEval, LlmUsageOut } from '../api/types'
import { Pill } from '../components/Badges'
import CategoryBadge from '../components/CategoryBadge'
import { QueryView } from '../components/QueryState'
import Section, { td, th } from '../components/Section'
import { formatRelative, formatSeconds } from '../components/format'

const n = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString())
const yesNo = (ok: boolean, yes = 'yes', no = 'no') => (ok ? <Pill tone="ok">{yes}</Pill> : <Pill tone="danger">{no}</Pill>)

export default function LlmOpsPage() {
  const ops = useLlmOps()
  const calls = useLlmCalls(100)
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">LLMOps</h1>

      <Section id="usage" title="Usage by purpose and model">
        <QueryView query={ops} loadingLabel="Loading usage…" isEmpty={(d) => d.usage.length === 0} empty="No LLM calls recorded yet.">
          {(d) => <UsageTable usage={d.usage} />}
        </QueryView>
      </Section>

      <Section id="eval" title="Latest evaluation">
        <QueryView
          query={ops}
          loadingLabel="Loading evaluation…"
          isEmpty={(d) => d.latest_eval == null}
          empty="No evaluation results yet. Run the eval suite in backend/evals."
        >
          {(d) => (d.latest_eval ? <EvalView ev={d.latest_eval} /> : null)}
        </QueryView>
      </Section>

      <Section id="calls" title="Recent LLM calls">
        <QueryView query={calls} loadingLabel="Loading calls…" empty="No LLM calls recorded yet.">
          {(data) => (
            <div className="-m-4 overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Recent LLM calls, newest first</caption>
                <thead className="border-b border-line">
                  <tr>
                    <th scope="col" className={th}>When</th>
                    <th scope="col" className={th}>Purpose</th>
                    <th scope="col" className={th}>Model</th>
                    <th scope="col" className={th}>Prompt</th>
                    <th scope="col" className={`${th} text-right`}>Tokens in/out</th>
                    <th scope="col" className={`${th} text-right`}>Latency</th>
                    <th scope="col" className={th}>Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {data.map((c) => (
                    <tr key={c.id} className="align-top">
                      <td className={`${td} text-xs whitespace-nowrap`}>
                        <time dateTime={c.created_at}>{formatRelative(c.created_at)}</time>
                      </td>
                      <th scope="row" className={`${td} text-left font-normal`}>
                        {c.purpose}
                        {c.run_id != null && <div className="text-xs text-muted">run #{c.run_id}</div>}
                      </th>
                      <td className={`${td} font-mono text-xs`}>
                        {c.provider}/{c.model}
                      </td>
                      <td className={`${td} font-mono text-xs`}>{c.prompt_version ?? '—'}</td>
                      <td className={`${td} text-right tabular`}>
                        {n(c.input_tokens)} / {n(c.output_tokens)}
                      </td>
                      <td className={`${td} text-right tabular`}>{formatSeconds(c.latency_ms / 1000)}</td>
                      <td className={td}>
                        {c.status === 'ok' ? (
                          <Pill tone="ok">ok</Pill>
                        ) : (
                          <details>
                            <summary className="cursor-pointer">
                              <Pill tone="danger">error</Pill>
                            </summary>
                            <p className="mt-1 max-w-md text-xs break-words">{c.error}</p>
                          </details>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryView>
      </Section>
    </div>
  )
}

function UsageTable({ usage }: { usage: LlmUsageOut[] }) {
  return (
    <div className="-m-4 overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">LLM usage by purpose and model</caption>
        <thead className="border-b border-line">
          <tr>
            <th scope="col" className={th}>Purpose</th>
            <th scope="col" className={th}>Model</th>
            <th scope="col" className={`${th} text-right`}>Calls</th>
            <th scope="col" className={`${th} text-right`}>Errors</th>
            <th scope="col" className={`${th} text-right`}>Input tokens</th>
            <th scope="col" className={`${th} text-right`}>Output tokens</th>
            <th scope="col" className={`${th} text-right`}>Avg latency</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {usage.map((u) => (
            <tr key={`${u.purpose}-${u.model}`}>
              <th scope="row" className={`${td} text-left font-normal`}>{u.purpose}</th>
              <td className={`${td} font-mono text-xs`}>{u.model}</td>
              <td className={`${td} text-right tabular`}>{n(u.calls)}</td>
              <td className={`${td} text-right tabular ${u.errors > 0 ? 'text-danger' : ''}`}>{n(u.errors)}</td>
              <td className={`${td} text-right tabular`}>{n(u.input_tokens)}</td>
              <td className={`${td} text-right tabular`}>{n(u.output_tokens)}</td>
              <td className={`${td} text-right tabular`}>{formatSeconds(u.avg_latency_ms / 1000)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function EvalView({ ev }: { ev: LatestEval }) {
  const correct = ev.results.filter((r) => r.correct).length
  return (
    <div className="space-y-3">
      <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
        {(
          [
            ['Accuracy', `${correct}/${ev.results.length}`],
            ['Mode', ev.mode],
            ['Model', ev.model],
            ['Prompt', ev.prompt_version],
            ['File', ev.file],
          ] as const
        ).map(([k, v]) => (
          <div key={k} className="flex gap-1">
            <dt>{k}</dt>
            <dd className="font-mono text-fg">{v ?? '—'}</dd>
          </div>
        ))}
      </dl>
      <div className="-mx-4 -mb-4 overflow-x-auto border-t border-line">
        <table className="w-full text-sm">
          <caption className="sr-only">Evaluation results by scenario</caption>
          <thead className="border-b border-line">
            <tr>
              <th scope="col" className={th}>Scenario</th>
              <th scope="col" className={th}>Expected</th>
              <th scope="col" className={th}>Diagnosed</th>
              <th scope="col" className={th}>Correct</th>
              <th scope="col" className={th}>Source</th>
              <th scope="col" className={th}>Citations</th>
              <th scope="col" className={`${th} text-right`}>Tool calls</th>
              <th scope="col" className={`${th} text-right`}>Tokens in/out</th>
              <th scope="col" className={`${th} text-right`}>Time</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {ev.results.map((r) => (
              <tr key={r.scenario}>
                <th scope="row" className={`${td} text-left font-normal`}>
                  {r.incident_id != null ? <Link to={`/incidents/${r.incident_id}`}>{r.scenario}</Link> : r.scenario}
                </th>
                <td className={td}>
                  <CategoryBadge category={r.expected} />
                </td>
                <td className={td}>
                  <CategoryBadge category={r.category} />
                </td>
                <td className={td}>{yesNo(r.correct)}</td>
                <td className={td}>
                  {r.source === 'fallback' ? <Pill>fallback</Pill> : <Pill tone="accent">{r.source ?? '—'}</Pill>}
                </td>
                <td className={td}>{yesNo(r.citations_valid, 'valid', 'invalid')}</td>
                <td className={`${td} text-right tabular`}>{n(r.tool_calls)}</td>
                <td className={`${td} text-right tabular whitespace-nowrap`}>
                  {n(r.input_tokens)} / {n(r.output_tokens)}
                </td>
                <td className={`${td} text-right tabular`}>{formatSeconds(r.seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
