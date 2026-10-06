/**
 * Data hooks.
 *
 * The important one is useRates. Prometheus counters only ever increase, so a
 * raw counter is close to meaningless on a dashboard -- it answers "how many
 * since this worker booted", not "what is happening now". Every live number
 * here is a delta between two consecutive polls divided by the real elapsed
 * time between them, never by the nominal poll interval: a slow response or a
 * backgrounded tab makes those two differ badly, and dividing by the nominal
 * value silently understates the rate.
 */

import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  fetchBloomStats,
  fetchFlags,
  fetchMetrics,
  fetchSettings,
  sumCounter,
  type MetricsSnapshot,
} from './api'

const POLL_MS = 2000

export function useSettings() {
  return useQuery({ queryKey: ['settings'], queryFn: fetchSettings, refetchInterval: 10_000 })
}

export function useFlags() {
  return useQuery({ queryKey: ['flags'], queryFn: fetchFlags, refetchInterval: POLL_MS })
}

export function useBloomStats() {
  return useQuery({ queryKey: ['bloom'], queryFn: fetchBloomStats, refetchInterval: POLL_MS })
}

export function useMetrics() {
  return useQuery({ queryKey: ['metrics'], queryFn: fetchMetrics, refetchInterval: POLL_MS })
}

export interface Rates {
  dbQps: number
  hitRate: number | null
  requestQps: number
  bloomRejectQps: number
  coalescedQps: number
  /** Totals since boot, for context beneath the live rates. */
  totals: { dbQueries: number; hits: number; misses: number; bloomRejects: number }
  /** History for the sparklines, newest last. */
  history: { t: number; dbQps: number; hitRate: number | null }[]
}

const HISTORY_POINTS = 60

function readTotals(snapshot: MetricsSnapshot) {
  const s = snapshot.samples
  return {
    dbQueries: sumCounter(s, 'cache_gateway_database_queries_total'),
    hits: sumCounter(s, 'cache_gateway_cache_operations_total', { key: 'outcome', value: 'hit' }),
    misses: sumCounter(s, 'cache_gateway_cache_operations_total', { key: 'outcome', value: 'miss' }),
    bloomRejects: sumCounter(s, 'cache_gateway_bloom_queries_total', { key: 'result', value: 'miss' }),
    coalesced: sumCounter(s, 'cache_gateway_coalesced_requests_total'),
    requests: sumCounter(s, 'cache_gateway_http_requests_total'),
  }
}

export function useRates(snapshot: MetricsSnapshot | undefined): Rates | null {
  const previous = useRef<{ totals: ReturnType<typeof readTotals>; at: number } | null>(null)
  const [rates, setRates] = useState<Rates | null>(null)
  const history = useRef<Rates['history']>([])

  useEffect(() => {
    if (!snapshot) return

    // The service's own scrape timestamp, not the browser's clock: it is the
    // instant the counters were actually read.
    const at = new Date(snapshot.scraped_at).getTime()
    const totals = readTotals(snapshot)
    const prev = previous.current
    previous.current = { totals, at }

    if (!prev) return

    const seconds = (at - prev.at) / 1000
    if (seconds <= 0) return

    const perSecond = (now: number, before: number) => {
      const delta = now - before
      // A counter that went backwards means the worker restarted and reset it.
      // Reporting a negative rate would be worse than reporting nothing.
      return delta < 0 ? 0 : delta / seconds
    }

    const hitDelta = totals.hits - prev.totals.hits
    const missDelta = totals.misses - prev.totals.misses
    const lookups = hitDelta + missDelta

    const next: Rates = {
      dbQps: perSecond(totals.dbQueries, prev.totals.dbQueries),
      // Null rather than 0 when nothing was looked up in the window: an idle
      // service has no hit rate, and drawing 0% would read as "every request
      // missed", which is the opposite of the truth.
      hitRate: lookups > 0 ? (hitDelta / lookups) * 100 : null,
      requestQps: perSecond(totals.requests, prev.totals.requests),
      bloomRejectQps: perSecond(totals.bloomRejects, prev.totals.bloomRejects),
      coalescedQps: perSecond(totals.coalesced, prev.totals.coalesced),
      totals: {
        dbQueries: totals.dbQueries,
        hits: totals.hits,
        misses: totals.misses,
        bloomRejects: totals.bloomRejects,
      },
      history: [],
    }

    history.current = [
      ...history.current,
      { t: at, dbQps: next.dbQps, hitRate: next.hitRate },
    ].slice(-HISTORY_POINTS)
    next.history = history.current

    setRates(next)
  }, [snapshot])

  return rates
}
