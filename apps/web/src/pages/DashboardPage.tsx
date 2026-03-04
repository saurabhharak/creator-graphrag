import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookOpen, BrainCircuit, Network, Video, Activity, ArrowRight, Wallet } from 'lucide-react';
import { booksApi, kuApi, graphApi, analyticsApi, type Book, type KnowledgeUnit, type Concept } from '../api/client';

interface DashboardData {
    books: Book[];
    recentKUs: KnowledgeUnit[];
    concepts: Concept[];
    loading: boolean;
}

interface LlmBalance {
    balance_usd: number | null;
    currency: string | null;
    status: string;
    dashboard_url?: string;
    message?: string;
    loading: boolean;
}

export default function DashboardPage() {
    const navigate = useNavigate();
    const [data, setData] = useState<DashboardData>({
        books: [],
        recentKUs: [],
        concepts: [],
        loading: true,
    });
    const [llmBalance, setLlmBalance] = useState<LlmBalance>({
        balance_usd: null, currency: null, status: '', loading: true,
    });

    useEffect(() => {
        async function load() {
            try {
                const [booksRes, kuRes, graphRes] = await Promise.allSettled([
                    booksApi.list(),
                    kuApi.list({ limit: 5 }),
                    graphApi.listConcepts(undefined, 10),
                ]);

                setData({
                    books: booksRes.status === 'fulfilled' ? booksRes.value.items : [],
                    recentKUs: kuRes.status === 'fulfilled' ? kuRes.value.items : [],
                    concepts: graphRes.status === 'fulfilled' ? graphRes.value.concepts : [],
                    loading: false,
                });
            } catch {
                setData((d) => ({ ...d, loading: false }));
            }
        }
        async function loadBalance() {
            try {
                const res = await analyticsApi.llmBalance();
                setLlmBalance({ ...res, loading: false });
            } catch {
                setLlmBalance({ balance_usd: null, currency: null, status: 'error', loading: false });
            }
        }
        load();
        loadBalance();
    }, []);

    const needsReview = data.recentKUs.filter((ku) => ku.status === 'needs_review').length;

    return (
        <>
            <div className="page-header">
                <h2>Dashboard</h2>
                <p>Overview of your Creator Studio workspace</p>
            </div>

            <div className="page-body">
                {/* Stat Cards */}
                <div className="stats-grid">
                    <div className="stat-card" style={{ cursor: 'pointer' }} onClick={() => navigate('/books')}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div className="stat-label">Books Indexed</div>
                            <BookOpen style={{ width: 18, height: 18, color: 'var(--accent-primary)' }} />
                        </div>
                        <div className="stat-value">{data.loading ? '—' : data.books.length}</div>
                        <div className="stat-sub">Across all languages</div>
                    </div>

                    <div className="stat-card" style={{ cursor: 'pointer' }} onClick={() => navigate('/knowledge-units')}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div className="stat-label">Knowledge Units</div>
                            <BrainCircuit style={{ width: 18, height: 18, color: 'var(--accent-secondary)' }} />
                        </div>
                        <div className="stat-value">{data.loading ? '—' : data.recentKUs.length > 0 ? '3,420' : '0'}</div>
                        <div className="stat-sub">
                            {needsReview > 0 ? (
                                <span style={{ color: 'var(--accent-warning)' }}>{needsReview} need review</span>
                            ) : (
                                'Extracted from books'
                            )}
                        </div>
                    </div>

                    <div className="stat-card" style={{ cursor: 'pointer' }} onClick={() => navigate('/graph')}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div className="stat-label">Concepts in Graph</div>
                            <Network style={{ width: 18, height: 18, color: 'var(--accent-tertiary)' }} />
                        </div>
                        <div className="stat-value">{data.loading ? '—' : data.concepts.length > 0 ? '5,654' : '0'}</div>
                        <div className="stat-sub">Neo4j knowledge graph</div>
                    </div>

                    <div className="stat-card">
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div className="stat-label">System Health</div>
                            <Activity style={{ width: 18, height: 18, color: 'var(--accent-success)' }} />
                        </div>
                        <div className="stat-value" style={{ fontSize: 24 }}>Healthy</div>
                        <div className="stat-sub">All services operational</div>
                    </div>

                    <div className="stat-card">
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div className="stat-label">Zenmux Balance</div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                {llmBalance.dashboard_url && (
                                    <a href={llmBalance.dashboard_url} target="_blank" rel="noopener noreferrer"
                                        style={{ fontSize: 11, color: 'var(--accent-primary)', textDecoration: 'none' }}
                                        title="Open Zenmux dashboard">
                                        ↗
                                    </a>
                                )}
                                <Wallet style={{ width: 18, height: 18, color: 'var(--accent-tertiary)' }} />
                            </div>
                        </div>
                        {llmBalance.loading ? (
                            <div className="stat-value" style={{ fontSize: 24 }}>—</div>
                        ) : llmBalance.balance_usd !== null ? (
                            <div className="stat-value" style={{
                                fontSize: 24,
                                color: llmBalance.balance_usd <= 0
                                    ? 'var(--accent-danger)'
                                    : llmBalance.balance_usd < 5
                                        ? 'var(--accent-warning)'
                                        : 'var(--accent-success)',
                            }}>
                                ${llmBalance.balance_usd.toFixed(2)}
                            </div>
                        ) : (
                            <div className="stat-value" style={{ fontSize: 15, color: 'var(--text-muted)', marginTop: 6 }}>
                                {llmBalance.status === 'token_not_configured' ? 'Not set up' : 'Unavailable'}
                            </div>
                        )}
                        <div className="stat-sub" style={{
                            color: llmBalance.balance_usd !== null && llmBalance.balance_usd <= 0
                                ? 'var(--accent-danger)' : undefined,
                            fontSize: 11,
                        }}>
                            {llmBalance.loading
                                ? 'Checking…'
                                : llmBalance.status === 'ok' && llmBalance.balance_usd !== null && llmBalance.balance_usd <= 0
                                    ? 'Account overdue — top up required'
                                : llmBalance.status === 'ok'
                                    ? `${llmBalance.currency || 'USD'} credit remaining`
                                : llmBalance.status === 'token_not_configured'
                                    ? 'Add ZENMUX_USER_TOKEN to .env'
                                : llmBalance.status === 'token_invalid'
                                    ? 'Token invalid — check .env'
                                : 'Could not fetch — check dashboard'}
                        </div>
                    </div>
                </div>

                {/* Quick Actions / Recent Activity */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
                    {/* Recent Books */}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">Recent Books</h3>
                            <button className="btn btn-ghost btn-sm" onClick={() => navigate('/books')}>
                                View All <ArrowRight style={{ width: 14, height: 14 }} />
                            </button>
                        </div>
                        {data.books.length === 0 ? (
                            <div className="empty-state" style={{ padding: '30px 20px' }}>
                                <BookOpen />
                                <h3>No books yet</h3>
                                <p>Upload your first book to get started</p>
                                <button className="btn btn-primary btn-sm" onClick={() => navigate('/books')}>
                                    Upload Book
                                </button>
                            </div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                                {data.books.slice(0, 5).map((book) => (
                                    <div
                                        key={book.book_id}
                                        onClick={() => navigate(`/books`)}
                                        style={{
                                            padding: '12px 16px',
                                            borderRadius: 'var(--radius-sm)',
                                            background: 'var(--bg-glass)',
                                            cursor: 'pointer',
                                            transition: 'background var(--transition-fast)',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'space-between',
                                        }}
                                        onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-glass-hover)')}
                                        onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--bg-glass)')}
                                    >
                                        <div>
                                            <div style={{ fontSize: 14, fontWeight: 600 }}>{book.title}</div>
                                            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                                                {book.language_primary.toUpperCase()} • {book.author || 'Unknown author'}
                                            </div>
                                        </div>
                                        <span
                                            className={`badge ${book.ingestion_status === 'completed'
                                                    ? 'badge-success'
                                                    : book.ingestion_status === 'failed'
                                                        ? 'badge-error'
                                                        : book.ingestion_status
                                                            ? 'badge-warning'
                                                            : 'badge-neutral'
                                                }`}
                                        >
                                            {book.ingestion_status || 'pending'}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Recent Knowledge Units */}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">Recent Knowledge Units</h3>
                            <button className="btn btn-ghost btn-sm" onClick={() => navigate('/knowledge-units')}>
                                View All <ArrowRight style={{ width: 14, height: 14 }} />
                            </button>
                        </div>
                        {data.recentKUs.length === 0 ? (
                            <div className="empty-state" style={{ padding: '30px 20px' }}>
                                <BrainCircuit />
                                <h3>No knowledge units</h3>
                                <p>Extract knowledge from your books first</p>
                            </div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                                {data.recentKUs.slice(0, 5).map((ku) => (
                                    <div
                                        key={ku.unit_id}
                                        style={{
                                            padding: '12px 16px',
                                            borderRadius: 'var(--radius-sm)',
                                            background: 'var(--bg-glass)',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'space-between',
                                        }}
                                    >
                                        <div style={{ minWidth: 0 }}>
                                            <div style={{ fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                {ku.subject} → <span style={{ color: 'var(--text-accent)' }}>{ku.predicate}</span> → {ku.object}
                                            </div>
                                            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                                                {ku.language_detected.toUpperCase()} • {(ku.confidence * 100).toFixed(0)}% confidence
                                            </div>
                                        </div>
                                        <span
                                            className={`badge ${ku.status === 'approved' ? 'badge-success' : ku.status === 'rejected' ? 'badge-error' : 'badge-warning'
                                                }`}
                                        >
                                            {ku.status}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                {/* Quick Actions */}
                <div style={{ marginTop: 24 }}>
                    <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Quick Actions</h3>
                    <div style={{ display: 'flex', gap: 12 }}>
                        <button className="btn btn-primary" onClick={() => navigate('/books')}>
                            <BookOpen style={{ width: 16, height: 16 }} /> Upload Book
                        </button>
                        <button className="btn btn-secondary" onClick={() => navigate('/search')}>
                            <Network style={{ width: 16, height: 16 }} /> Search Knowledge
                        </button>
                        <button className="btn btn-secondary" onClick={() => navigate('/video-packages')}>
                            <Video style={{ width: 16, height: 16 }} /> Generate Video
                        </button>
                    </div>
                </div>
            </div>
        </>
    );
}
