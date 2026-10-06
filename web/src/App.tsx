import { useCallback, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Meter, Panel, Sparkline, Stat, Toggle } from './components'
import { useBloomStats, useFlags, useMetrics, useRates, useSettings } from './hooks'
import { fetchProduct, patchFlags, rebuildBloom } from './api'

interface Probe {
  id: number
  status: number
  ms: number
  at: string
}

export default function App() {
  const queryClient = useQueryClient()
  const settings = useSettings()
  const flags = useFlags()
  const bloom = useBloomStats()
  const metrics = useMetrics()
  const rates = useRates(metrics.data)

  const [probes, setProbes] = useState<Probe[]>([])
  const [error, setError] = useState<string | null>(null)

  const toggle = useMutation({
    mutationFn: patchFlags,
    onSuccess: () => {
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['flags'] })
      void queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const rebuild = useMutation({
    mutationFn: rebuildBloom,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['bloom'] }),
    onError: (e: Error) => setError(e.message),
  })

  const probe = useCallback(async (id: number) => {
    const result = await fetchProduct(id)
    setProbes((prev) =>
      [{ id, status: result.status, ms: result.ms, at: new Date().toLocaleTimeString() }, ...prev].slice(0, 12),
    )
  }, [])

  const workers = flags.data?.web_concurrency ?? settings.data?.web_concurrency ?? 1

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <h1>Cache Gateway</h1>
          <p>
            Four cache failure modes, each with a switch that turns its protection off.
            {settings.data && (
              <> Environment <code>{settings.data.environment}</code>, {workers} workers.</>
            )}
          </p>
        </div>
        <div className={`pulse ${metrics.isError ? 'pulse--down' : 'pulse--up'}`}>
          {metrics.isError ? 'service unreachable' : 'live'}
        </div>
      </header>

      {error && (
        <div className="banner banner--error" role="alert">
          {error}
          <button onClick={() => setError(null)}>dismiss</button>
        </div>
      )}

      <div className="stats">
        <Stat
          label="Database QPS"
          value={rates ? rates.dbQps.toFixed(1) : '—'}
          hint="the number every protection exists to move"
          tone={rates && rates.dbQps > 100 ? 'bad' : 'good'}
        />
        <Stat
          label="Cache hit rate"
          value={rates?.hitRate != null ? rates.hitRate.toFixed(1) : '—'}
          unit={rates?.hitRate != null ? '%' : undefined}
          hint={rates?.hitRate == null ? 'no lookups in the last window' : 'of lookups in the last window'}
        />
        <Stat label="Request rate" value={rates ? rates.requestQps.toFixed(1) : '—'} unit="/s" />
        <Stat
          label="Coalesced"
          value={rates ? rates.coalescedQps.toFixed(1) : '—'}
          unit="/s"
          hint="requests that waited on a leader instead of querying"
        />
      </div>

      <div className="grid">
        <Panel
          title="Database load"
          subtitle="Rates are deltas between consecutive scrapes, divided by elapsed time."
        >
          <Sparkline points={(rates?.history ?? []).map((h) => h.dbQps)} />
          <div className="legend">
            <span>DB QPS over the last {rates?.history.length ?? 0} samples</span>
            <span>{rates ? `${rates.totals.dbQueries.toLocaleString()} total since boot` : ''}</span>
          </div>
          <Sparkline points={(rates?.history ?? []).map((h) => h.hitRate)} max={100} />
          <div className="legend">
            <span>Hit rate, 0–100%</span>
          </div>
        </Panel>

        <Panel
          title="Failure-mode switches"
          subtitle={`Per worker, by construction. A change reaches one of ${workers} workers at a time — click until it settles.`}
        >
          {flags.data ? (
            <>
              <Toggle
                label="Bloom filter"
                description="Off: lookups for absent ids reach Postgres (penetration)"
                checked={flags.data.bloom_enabled}
                disabled={toggle.isPending}
                onChange={(next) => toggle.mutate({ bloom_enabled: next })}
              />
              <Toggle
                label="Request coalescing"
                description="Off: concurrent misses on one key each query (stampede)"
                checked={flags.data.coalesce_enabled}
                disabled={toggle.isPending}
                onChange={(next) => toggle.mutate({ coalesce_enabled: next })}
              />
              <Toggle
                label="Delete-on-write"
                description="Off: writes leave stale entries until TTL (consistency)"
                checked={flags.data.cache_invalidate_on_write}
                disabled={toggle.isPending}
                onChange={(next) => toggle.mutate({ cache_invalidate_on_write: next })}
              />
              <label className="slider">
                <span>
                  <strong>TTL jitter</strong>
                  <em>0%: keys written together expire together (avalanche)</em>
                </span>
                <input
                  type="range"
                  min={0}
                  max={50}
                  value={flags.data.cache_ttl_jitter_pct}
                  disabled={toggle.isPending}
                  onChange={(e) =>
                    toggle.mutate({ cache_ttl_jitter_pct: Number(e.target.value) })
                  }
                />
                <output>{flags.data.cache_ttl_jitter_pct}%</output>
              </label>
              <p className="note">
                Answered by worker <code>{flags.data.worker_pid}</code>. These flags live in
                process memory, so each worker holds its own copy.
              </p>
            </>
          ) : (
            <p className="note">loading…</p>
          )}
        </Panel>

        <Panel title="Bloom filter" subtitle="Penetration protection, 1.2 MB per worker.">
          {bloom.data ? (
            <>
              <Meter
                fraction={bloom.data.saturation}
                caption={`${(bloom.data.saturation * 100).toFixed(1)}% saturated — ${bloom.data.items_added.toLocaleString()} of ${bloom.data.expected_items.toLocaleString()}`}
              />
              <div className="kv">
                <div>
                  <dt>Measured FP rate</dt>
                  <dd>
                    {bloom.data.total_lookups > 0
                      ? `${(bloom.data.measured_fp_rate * 100).toFixed(2)}%`
                      : 'no lookups yet'}
                  </dd>
                </div>
                <div>
                  <dt>Target FP rate</dt>
                  <dd>{(bloom.data.target_fp_rate * 100).toFixed(2)}%</dd>
                </div>
                <div>
                  <dt>Rejects</dt>
                  <dd>{rates ? `${rates.bloomRejectQps.toFixed(1)}/s` : '—'}</dd>
                </div>
                <div>
                  <dt>Size</dt>
                  <dd>{(bloom.data.m_bits / 8 / 1024 / 1024).toFixed(2)} MB · {bloom.data.k_hashes} hashes</dd>
                </div>
              </div>
              <button
                className="btn"
                onClick={() => rebuild.mutate()}
                disabled={rebuild.isPending}
              >
                {rebuild.isPending ? 'rebuilding…' : 'Rebuild from Postgres'}
              </button>
              <p className="note">
                A Bloom filter cannot delete. Deleted products linger as false positives until
                a rebuild, which is a full table scan — cheap here, not free.
              </p>
            </>
          ) : (
            <p className="note">loading…</p>
          )}
        </Panel>

        <Panel title="Probe" subtitle="Send a read and watch which path answers it.">
          <div className="probes">
            <button className="btn" onClick={() => void probe(42)}>
              Existing id (42)
            </button>
            <button className="btn" onClick={() => void probe(999_999_999)}>
              Absent id — penetration
            </button>
            <button
              className="btn"
              onClick={() => {
                for (let i = 0; i < 25; i++) void probe(Math.floor(Math.random() * 1000) + 1)
              }}
            >
              25 random reads
            </button>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>time</th>
                <th>id</th>
                <th>status</th>
                <th>latency</th>
              </tr>
            </thead>
            <tbody>
              {probes.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    no requests yet
                  </td>
                </tr>
              )}
              {probes.map((p, i) => (
                <tr key={i}>
                  <td className="muted">{p.at}</td>
                  <td>{p.id}</td>
                  <td className={p.status === 200 ? 'ok' : 'warn'}>{p.status}</td>
                  <td>{p.ms.toFixed(1)} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      <footer className="foot">
        Measured results:{' '}
        <a href="https://github.com/sahil00016/cache-gateway/tree/main/benchmarks">benchmarks/</a>{' '}
        · Counters come from the service's own <code>/v1/admin/metrics</code>, aggregated across
        workers.
      </footer>
    </div>
  )
}
