import { useEffect, useState } from 'react';
import { graphApi, type Concept } from '../api/client';
import { Network, Search, ArrowRight, Loader } from 'lucide-react';
import toast from 'react-hot-toast';

export default function GraphPage() {
    const [concepts, setConcepts] = useState<Concept[]>([]);
    const [loading, setLoading] = useState(true);
    const [query, setQuery] = useState('');
    const [selectedConcept, setSelectedConcept] = useState<string | null>(null);
    const [detail, setDetail] = useState<{
        canonical_key: string;
        label_en: string | null;
        aliases: string[];
        edge_summary: { type: string; neighbor_key: string; neighbor_label: string }[];
        mermaid_spec: string;
    } | null>(null);
    const [detailLoading, setDetailLoading] = useState(false);

    const loadConcepts = async (q?: string) => {
        setLoading(true);
        try {
            const res = await graphApi.listConcepts(q || undefined, 50);
            setConcepts(res.concepts);
        } catch {
            toast.error('Failed to load concepts');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { loadConcepts(); }, []);

    const handleSearch = (e: React.FormEvent) => {
        e.preventDefault();
        loadConcepts(query);
    };

    const handleSelectConcept = async (key: string) => {
        setSelectedConcept(key);
        setDetailLoading(true);
        try {
            const data = await graphApi.getConcept(key);
            setDetail(data);
        } catch {
            toast.error('Failed to load concept');
        } finally {
            setDetailLoading(false);
        }
    };

    return (
        <>
            <div className="page-header">
                <h2>Knowledge Graph</h2>
                <p>Explore concepts and their relationships in Neo4j</p>
            </div>

            <div className="page-body">
                <form onSubmit={handleSearch} className="toolbar">
                    <div className="search-input">
                        <Search />
                        <input
                            placeholder="Search concepts (e.g., compost, soil, vermicompost)..."
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            style={{ minWidth: 360 }}
                        />
                    </div>
                    <button type="submit" className="btn btn-primary btn-sm">
                        Search
                    </button>
                </form>

                <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 20, minHeight: 500 }}>
                    {/* Concept List */}
                    <div className="card" style={{ overflow: 'hidden' }}>
                        <div className="card-header">
                            <h3 className="card-title">Concepts</h3>
                            <span className="badge badge-neutral">{concepts.length}</span>
                        </div>

                        {loading ? (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                {[1, 2, 3, 4, 5].map((i) => (
                                    <div key={i} className="skeleton" style={{ height: 44 }} />
                                ))}
                            </div>
                        ) : concepts.length === 0 ? (
                            <div className="empty-state" style={{ padding: '40px 20px' }}>
                                <Network />
                                <h3>No concepts found</h3>
                                <p>Try a different search term</p>
                            </div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 500, overflowY: 'auto' }}>
                                {concepts.map((c) => (
                                    <button
                                        key={c.canonical_key}
                                        className={`nav-link ${selectedConcept === c.canonical_key ? 'active' : ''}`}
                                        onClick={() => handleSelectConcept(c.canonical_key)}
                                        style={{ textAlign: 'left' }}
                                    >
                                        <Network style={{ width: 14, height: 14, flexShrink: 0 }} />
                                        <div style={{ minWidth: 0 }}>
                                            <div style={{ fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                {c.label_en || c.canonical_key}
                                            </div>
                                            {c.label_mr && (
                                                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{c.label_mr}</div>
                                            )}
                                        </div>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Concept Detail */}
                    <div className="card">
                        {!selectedConcept ? (
                            <div className="empty-state" style={{ padding: '60px 20px' }}>
                                <Network />
                                <h3>Select a concept</h3>
                                <p>Click on a concept to see its relationships and details</p>
                            </div>
                        ) : detailLoading ? (
                            <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
                                <Loader style={{ width: 24, height: 24, animation: 'spin 1s linear infinite', color: 'var(--accent-primary)' }} />
                            </div>
                        ) : detail ? (
                            <div>
                                <h3 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>
                                    {detail.label_en || detail.canonical_key}
                                </h3>
                                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 20 }}>
                                    Key: {detail.canonical_key}
                                </div>

                                {detail.aliases.length > 0 && (
                                    <div style={{ marginBottom: 20 }}>
                                        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 6 }}>
                                            ALIASES
                                        </div>
                                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                            {detail.aliases.map((a) => (
                                                <span key={a} className="badge badge-info">{a}</span>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                <div style={{ marginBottom: 16 }}>
                                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 8 }}>
                                        RELATIONSHIPS ({detail.edge_summary.length})
                                    </div>
                                    {detail.edge_summary.length === 0 ? (
                                        <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>No relationships found</div>
                                    ) : (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                            {detail.edge_summary.map((edge, i) => (
                                                <div
                                                    key={i}
                                                    onClick={() => handleSelectConcept(edge.neighbor_key)}
                                                    style={{
                                                        padding: '10px 14px',
                                                        borderRadius: 'var(--radius-sm)',
                                                        background: 'var(--bg-glass)',
                                                        display: 'flex',
                                                        alignItems: 'center',
                                                        gap: 10,
                                                        cursor: 'pointer',
                                                        transition: 'background var(--transition-fast)',
                                                    }}
                                                    onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-glass-hover)')}
                                                    onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--bg-glass)')}
                                                >
                                                    <span className="badge badge-info" style={{ fontSize: 10 }}>
                                                        {edge.type}
                                                    </span>
                                                    <ArrowRight style={{ width: 12, height: 12, color: 'var(--text-muted)' }} />
                                                    <span style={{ fontSize: 13, fontWeight: 500 }}>
                                                        {edge.neighbor_label || edge.neighbor_key}
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                {/* Mermaid spec */}
                                <div>
                                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 8 }}>
                                        GRAPH SPEC
                                    </div>
                                    <pre
                                        style={{
                                            padding: 16,
                                            borderRadius: 'var(--radius-sm)',
                                            background: 'var(--bg-glass)',
                                            fontSize: 12,
                                            color: 'var(--text-secondary)',
                                            overflow: 'auto',
                                            maxHeight: 200,
                                        }}
                                    >
                                        {detail.mermaid_spec}
                                    </pre>
                                </div>
                            </div>
                        ) : null}
                    </div>
                </div>
            </div>
        </>
    );
}
