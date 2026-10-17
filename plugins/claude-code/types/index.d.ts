// The type contract of the delegate plugin: the part of the `delegate status --json` snapshot that it shows, and the
// value that it keeps in `$.state`.

export type DelegateTicket = {
  id: string
  state: string
  phase: string | null
  elapsed_seconds: number
  tokens_new: number | null
  tokens_cache: number | null
  idle_seconds: number | null
  gate: string | null
  gate_idle_seconds: number | null
}

export type DelegateSnapshot = {
  run_id: string
  state: string
  percent: number
  eta: string | null
  tokens_new: number | null
  tokens_cache: number | null
  phase: string | null
  tickets: DelegateTicket[]
}

declare module 'claude-code' {
  interface PluginState {
    delegate: { snapshot: DelegateSnapshot | null }
  }
}
