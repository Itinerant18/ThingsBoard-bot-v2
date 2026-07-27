/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Comma-separated list of additional origins trusted to deliver the TB auth token via postMessage. */
  readonly VITE_TB_ALLOWED_ORIGINS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
