/**
 * Typed client for the cache-gateway admin API.
 *
 * Every response is wrapped in the service's success envelope, so unwrapping
 * happens here once rather than at each call site.
 */

export interface Envelope<T> {
  success: boolean
  data: T
}

export interface EffectiveSettings {
  environment: string
  web_concurrency: number
  write_strategy: string
  cache_invalidate_on_write: boolean
  cache_ttl_seconds: number
  cache_ttl_jitter_pct: number
  cache_ttl_jitter_seconds: number
  cache_key_prefix: string
  bloom_enabled: boolean
  bloom_expected_items: number
  bloom_fp_rate: number
  coalesce_enabled: boolean
  redis_lock_enabled: boolean
  redis_lock_ttl_ms: number
}

export interface RuntimeFlags {
  bloom_enabled: boolean
  coalesce_enabled: boolean
  cache_invalidate_on_write: boolean
  cache_ttl_jitter_pct: number
  worker_pid: number
  web_concurrency: number
}

export interface MetricSample {
  name: string
  labels: Record<string, string>
  value: number
}

export interface MetricsSnapshot {
  scraped_at: string
  multiprocess: boolean
  samples: MetricSample[]
}

export interface BloomStats {
  enabled: boolean
  expected_items: number
  items_added: number
  saturation: number
  target_fp_rate: number
  measured_fp_rate: number
  total_lookups: number
  false_positives: number
  m_bits: number
  k_hashes: number
}

export type FlagName =
  | 'bloom_enabled'
  | 'coalesce_enabled'
  | 'cache_invalidate_on_write'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    // Surface the service's own error message rather than a bare status code:
    // the admin endpoints explain *why* a patch was rejected, and swallowing
    // that turns a clear message into a silent failed toggle.
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { error?: { message?: string }; detail?: string }
      detail = body.error?.message ?? body.detail ?? detail
    } catch {
      // response had no JSON body; the status line is all we have
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function fetchSettings(): Promise<EffectiveSettings> {
  const body = await request<Envelope<EffectiveSettings>>('/v1/admin/stats')
  return body.data
}

export async function fetchFlags(): Promise<RuntimeFlags> {
  const body = await request<Envelope<RuntimeFlags>>('/v1/admin/flags')
  return body.data
}

export async function patchFlags(changes: Partial<Record<string, boolean | number>>): Promise<RuntimeFlags> {
  const body = await request<Envelope<RuntimeFlags>>('/v1/admin/flags', {
    method: 'PATCH',
    body: JSON.stringify(changes),
  })
  return body.data
}

export async function fetchMetrics(): Promise<MetricsSnapshot> {
  const body = await request<Envelope<MetricsSnapshot>>('/v1/admin/metrics')
  return body.data
}

export async function fetchBloomStats(): Promise<BloomStats> {
  const body = await request<Envelope<BloomStats>>('/v1/admin/bloom/stats')
  return body.data
}

export async function rebuildBloom(): Promise<unknown> {
  return request('/v1/admin/bloom/rebuild', { method: 'POST' })
}

export async function fetchProduct(id: number): Promise<{ status: number; ms: number }> {
  const started = performance.now()
  const response = await fetch(`/v1/products/${id}`)
  return { status: response.status, ms: performance.now() - started }
}

/** Sum one counter family, optionally restricted to a single label value. */
export function sumCounter(
  samples: MetricSample[],
  name: string,
  label?: { key: string; value: string },
): number {
  return samples
    .filter((s) => s.name === name)
    .filter((s) => !label || s.labels[label.key] === label.value)
    .reduce((total, s) => total + s.value, 0)
}
