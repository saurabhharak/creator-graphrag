/** Lightweight fetch wrapper for the Creator GraphRAG API. */

const API_BASE = import.meta.env.VITE_API_URL || '/v1';

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown;
}

class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const token = localStorage.getItem('access_token');
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(opts.headers as Record<string, string> || {}),
  };

  const res = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });

  if (res.status === 401) {
    // Try refresh once
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      return request<T>(path, opts);
    }
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    window.location.href = '/login';
    throw new ApiError(401, 'Session expired');
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail || res.statusText);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

async function tryRefreshToken(): Promise<boolean> {
  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken) return false;

  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    localStorage.setItem('access_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export const authApi = {
  login: (email: string, password: string) =>
    request<{ access_token: string; refresh_token: string; expires_in: number }>('/auth/login', {
      method: 'POST',
      body: { email, password },
    }),

  register: (email: string, password: string, display_name: string) =>
    request<{ access_token: string; refresh_token: string }>('/auth/register', {
      method: 'POST',
      body: { email, password, display_name },
    }),

  me: () => request<{ user_id: string; email: string; display_name: string; role: string }>('/auth/me'),
};

// ── Books ─────────────────────────────────────────────────────────────────────
export interface Book {
  book_id: string;
  title: string;
  author: string | null;
  language_primary: string;
  tags: string[];
  ingestion_status: string | null;
  chunk_count: number;
  unit_approval_rate: number | null;
  created_at: string;
  updated_at: string;
}

export interface BookDetail extends Book {
  year: number | null;
  edition: string | null;
  publisher: string | null;
  isbn: string | null;
  visibility: string;
  usage_rights: string;
  ingestion_stage: string | null;
  ingestion_progress: number | null;
}

export const booksApi = {
  list: (cursor?: string, language?: string) => {
    const params = new URLSearchParams();
    if (cursor) params.set('cursor', cursor);
    if (language) params.set('language', language);
    const qs = params.toString();
    return request<{ items: Book[]; next_cursor: string | null }>(`/books${qs ? '?' + qs : ''}`);
  },

  get: (id: string) => request<BookDetail>(`/books/${id}`),

  create: (data: { title: string; language_primary: string; author?: string; tags?: string[] }) =>
    request<{ book_id: string; upload: { url: string } }>('/books', { method: 'POST', body: data }),

  delete: (id: string) => request<void>(`/books/${id}`, { method: 'DELETE' }),
};

// ── Knowledge Units ───────────────────────────────────────────────────────────
export interface KnowledgeUnit {
  unit_id: string;
  source_book_id: string;
  type: string;
  language_detected: string;
  subject: string | null;
  predicate: string | null;
  object: string | null;
  confidence: number;
  status: string;
  canonical_key: string | null;
  evidence: unknown[];
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export const kuApi = {
  list: (params: { status?: string; book_id?: string; limit?: number; cursor?: string }) => {
    const sp = new URLSearchParams();
    if (params.status) sp.set('status', params.status);
    if (params.book_id) sp.set('book_id', params.book_id);
    if (params.limit) sp.set('limit', String(params.limit));
    if (params.cursor) sp.set('cursor', params.cursor);
    return request<{ items: KnowledgeUnit[]; next_cursor: string | null }>(`/knowledge-units?${sp}`);
  },

  update: (id: string, body: { status?: string; subject?: string; predicate?: string; object?: string }) =>
    request<KnowledgeUnit>(`/knowledge-units/${id}`, { method: 'PATCH', body }),

  bulkUpdate: (unit_ids: string[], action: 'approve' | 'reject') =>
    request<{ succeeded: number; failed: number }>('/knowledge-units/bulk-update', {
      method: 'POST',
      body: { unit_ids, action },
    }),
};

// ── Graph ─────────────────────────────────────────────────────────────────────
export interface Concept {
  canonical_key: string;
  label_en: string | null;
  label_mr: string | null;
  label_hi: string | null;
}

export const graphApi = {
  listConcepts: (q?: string, limit = 50) => {
    const sp = new URLSearchParams();
    if (q) sp.set('q', q);
    sp.set('limit', String(limit));
    return request<{ concepts: Concept[] }>(`/graph/concepts?${sp}`);
  },

  getConcept: (key: string) =>
    request<{
      canonical_key: string;
      label_en: string | null;
      aliases: string[];
      edge_summary: { type: string; neighbor_key: string; neighbor_label: string }[];
      mermaid_spec: string;
    }>(`/graph/concepts/${encodeURIComponent(key)}`),

  getNeighbors: (key: string, maxHops = 2) =>
    request<{ nodes: { canonical_key: string; label_en: string }[]; edges: { type: string }[] }>(
      `/graph/concepts/${encodeURIComponent(key)}/neighbors?max_hops=${maxHops}`
    ),
};

// ── Search ────────────────────────────────────────────────────────────────────
export interface SearchResult {
  chunk_id: string;
  book_id: string;
  book_name: string | null;
  chunk_type: string;
  language_detected: string;
  page_start: number | null;
  page_end: number | null;
  score: number;
  text_preview: string;
}

export const searchApi = {
  search: (query: string, topK = 10, graphEnable = false) =>
    request<{ query: string; total: number; results: SearchResult[]; graph_plan: unknown }>('/search', {
      method: 'POST',
      body: { query, top_k: topK, graph: { enable: graphEnable, max_hops: 2 } },
    }),
};

// ── Video Packages ────────────────────────────────────────────────────────────
export const videoApi = {
  list: (cursor?: string) => {
    const sp = new URLSearchParams();
    if (cursor) sp.set('cursor', cursor);
    return request<{ items: unknown[]; next_cursor: string | null }>(`/video-packages?${sp}`);
  },

  generate: (data: { topic: string; format: string; audience_level: string; language_mode: string }) =>
    request<unknown>('/video-packages', { method: 'POST', body: data }),
};

// ── Health ────────────────────────────────────────────────────────────────────
export const healthApi = {
  ready: () => request<{ status: string; services: Record<string, string> }>('/health/ready'),
};

export { ApiError };
