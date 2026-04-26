/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set on Vercel: public Flask base URL, no trailing slash. */
  readonly VITE_API_BASE?: string;
}
