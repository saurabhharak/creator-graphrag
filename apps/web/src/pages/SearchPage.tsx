import { useState } from 'react';
import { searchApi, type SearchResult } from '../api/client';
import { Search as SearchIcon, BookOpen, Globe, Loader } from 'lucide-react';
import toast from 'react-hot-toast';

const LANGUAGES = [
    { value: '', label: 'All languages' },
    { value: 'en', label: 'English' },
    { value: 'mr', label: 'Marathi' },
    { value: 'hi', label: 'Hindi' },
];

const CHUNK_TYPES = [
    { value: '', label: 'All types' },
    { value: 'paragraph', label: 'Paragraph' },
    { value: 'heading', label: 'Heading' },
    { value: 'list_item', label: 'List item' },
    { value: 'table_row', label: 'Table row' },
];

const selectStyle: React.CSSProperties = {
    background: 'var(--bg-secondary)',
    border: '1px solid var(--border-primary)',
    color: 'var(--text-primary)',
    borderRadius: 8,
    padding: '4px 10px',
    fontSize: 13,
    cursor: 'pointer',
};

export default function SearchPage() {
    const [query, setQuery] = useState('');
    const [results, setResults] = useState<SearchResult[]>([]);
    const [loading, setLoading] = useState(false);
    const [graphEnable, setGraphEnable] = useState(false);
    const [searched, setSearched] = useState(false);
    const [language, setLanguage] = useState('');
    const [chunkType, setChunkType] = useState('');

    const handleSearch = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!query.trim()) return;
        setLoading(true);
        setSearched(true);
        try {
            const filters: Parameters<typeof searchApi.search>[3] = {};
            if (language) filters.languages = [language];
            if (chunkType) filters.chunk_types = [chunkType];
            const res = await searchApi.search(query, 15, graphEnable, filters);
            setResults(res.results);
        } catch (err) {
            toast.error(err instanceof Error ? err.message : 'Search failed');
        } finally {
            setLoading(false);
        }
    };

    return (
        <>
            <div className="page-header">
                <h2>Search Knowledge Base</h2>
                <p>Multilingual vector search with graph-augmented context</p>
            </div>

            <div className="page-body">
                <form onSubmit={handleSearch} style={{ marginBottom: 24 }}>
                    <div style={{ display: 'flex', gap: 12 }}>
                        <div className="search-input" style={{ flex: 1 }}>
                            <SearchIcon />
                            <input
                                placeholder="Ask a question in any language (e.g., मातीची सुपीकता कशी वाढवायची?)..."
                                value={query}
                                onChange={(e) => setQuery(e.target.value)}
                                style={{ minWidth: 0, width: '100%' }}
                            />
                        </div>
                        <button type="submit" className="btn btn-primary" disabled={loading}>
                            {loading
                                ? <Loader style={{ width: 16, height: 16, animation: 'spin 1s linear infinite' }} />
                                : 'Search'}
                        </button>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: 20, marginTop: 12, flexWrap: 'wrap' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <label style={{ fontSize: 13, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                                Language
                            </label>
                            <select value={language} onChange={(e) => setLanguage(e.target.value)} style={selectStyle}>
                                {LANGUAGES.map((l) => (
                                    <option key={l.value} value={l.value}>{l.label}</option>
                                ))}
                            </select>
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <label style={{ fontSize: 13, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                                Content type
                            </label>
                            <select value={chunkType} onChange={(e) => setChunkType(e.target.value)} style={selectStyle}>
                                {CHUNK_TYPES.map((t) => (
                                    <option key={t.value} value={t.value}>{t.label}</option>
                                ))}
                            </select>
                        </div>

                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-secondary)', cursor: 'pointer' }}>
                            <input
                                type="checkbox"
                                checked={graphEnable}
                                onChange={(e) => setGraphEnable(e.target.checked)}
                                style={{ cursor: 'pointer' }}
                            />
                            Graph-augmented context
                        </label>
                    </div>
                </form>

                {loading ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                        {[1, 2, 3].map((i) => (
                            <div key={i} className="skeleton" style={{ height: 120 }} />
                        ))}
                    </div>
                ) : results.length === 0 && searched ? (
                    <div className="empty-state">
                        <SearchIcon />
                        <h3>No results found</h3>
                        <p>Try a different query or check that Ollama is running for embedding</p>
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                        {results.map((r) => (
                            <div key={r.chunk_id} className="card">
                                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 8 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        <BookOpen style={{ width: 14, height: 14, color: 'var(--accent-primary)' }} />
                                        <span style={{ fontSize: 13, fontWeight: 600 }}>{r.book_name || 'Unknown Book'}</span>
                                        {r.page_start && (
                                            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                                                p. {r.page_start}{r.page_end && r.page_end !== r.page_start ? `–${r.page_end}` : ''}
                                            </span>
                                        )}
                                    </div>
                                    <div style={{ display: 'flex', gap: 6 }}>
                                        <span className="badge badge-info">
                                            <Globe style={{ width: 10, height: 10 }} />
                                            {r.language_detected.toUpperCase()}
                                        </span>
                                        <span className="badge badge-neutral">
                                            {(r.score * 100).toFixed(0)}% match
                                        </span>
                                        {r.chunk_type && r.chunk_type !== 'paragraph' && (
                                            <span className="badge badge-neutral">{r.chunk_type}</span>
                                        )}
                                    </div>
                                </div>
                                <p style={{ fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.7 }}>
                                    {r.text_preview}
                                </p>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </>
    );
}
