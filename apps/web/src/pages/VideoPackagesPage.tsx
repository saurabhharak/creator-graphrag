import { useState, useEffect, useCallback } from 'react';
import { videoApi, VideoPackage, VideoPackageFull } from '../api/client';
import {
    Video, Sparkles, Loader, Clock, FileText, Eye, Layers, AlertTriangle,
    ChevronDown, ChevronUp, Download, BookOpen, BarChart3, Film, ArrowLeft,
    Plus, RefreshCw,
} from 'lucide-react';
import toast from 'react-hot-toast';

type View = 'list' | 'generate' | 'detail';
type Tab = 'script' | 'storyboard' | 'visuals' | 'citations';

export default function VideoPackagesPage() {
    const [view, setView] = useState<View>('list');

    // List state
    const [packages, setPackages] = useState<VideoPackage[]>([]);
    const [nextCursor, setNextCursor] = useState<string | null>(null);
    const [loadingList, setLoadingList] = useState(true);

    // Generate form state
    const [topic, setTopic] = useState('');
    const [format, setFormat] = useState('explainer');
    const [audience, setAudience] = useState('beginner');
    const [langMode, setLangMode] = useState('en');
    const [tone, setTone] = useState('teacher');
    const [generating, setGenerating] = useState(false);

    // Detail state
    const [detail, setDetail] = useState<VideoPackageFull | null>(null);
    const [loadingDetail, setLoadingDetail] = useState(false);
    const [activeTab, setActiveTab] = useState<Tab>('script');
    const [selectedScene, setSelectedScene] = useState<number | null>(null);

    const loadList = useCallback(async (cursor?: string) => {
        setLoadingList(true);
        try {
            const res = await videoApi.list(cursor);
            setPackages(prev => cursor ? [...prev, ...res.items] : res.items);
            setNextCursor(res.next_cursor);
        } catch {
            toast.error('Failed to load video packages');
        } finally {
            setLoadingList(false);
        }
    }, []);

    useEffect(() => { loadList(); }, [loadList]);

    const openDetail = async (pkg: VideoPackage) => {
        setView('detail');
        setActiveTab('script');
        setSelectedScene(null);
        setLoadingDetail(true);
        try {
            setDetail(await videoApi.get(pkg.video_id));
        } catch {
            toast.error('Failed to load package');
            setView('list');
        } finally {
            setLoadingDetail(false);
        }
    };

    const handleGenerate = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!topic.trim()) return;
        setGenerating(true);
        try {
            const res = await videoApi.generate({ topic, format, audience_level: audience, language_mode: langMode, tone });
            setPackages(prev => [res as unknown as VideoPackage, ...prev]);
            setDetail(res);
            setActiveTab('script');
            setSelectedScene(null);
            setView('detail');
            setTopic('');
            toast.success('Video package generated!');
        } catch (err) {
            toast.error(err instanceof Error ? err.message : 'Generation failed');
        } finally {
            setGenerating(false);
        }
    };

    const handleExport = () => {
        if (!detail) return;
        const blob = new Blob([JSON.stringify(detail, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = Object.assign(document.createElement('a'), { href: url, download: `video-package-${detail.video_id}.json` });
        a.click();
        URL.revokeObjectURL(url);
        toast.success('Exported as JSON');
    };

    const totalDuration = detail?.storyboard?.scenes?.reduce((s, sc) => s + sc.duration_sec, 0) || 0;
    const evidenceForScene = (n: number) => detail?.evidence_map?.paragraphs?.find(p => p.scene_number === n);

    /* ── LIST ── */
    if (view === 'list') return (
        <>
            <div className="page-header">
                <h2>Video Packages</h2>
                <p>Generate and manage evidence-backed video scripts</p>
            </div>
            <div className="page-body">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                    <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                        {loadingList ? 'Loading…' : `${packages.length} package${packages.length !== 1 ? 's' : ''}`}
                    </span>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary" onClick={() => loadList()} style={{ fontSize: 13 }}>
                            <RefreshCw style={{ width: 14, height: 14 }} /> Refresh
                        </button>
                        <button className="btn btn-primary" onClick={() => setView('generate')} style={{ fontSize: 13 }}>
                            <Plus style={{ width: 14, height: 14 }} /> Generate New
                        </button>
                    </div>
                </div>

                {loadingList && packages.length === 0 ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                        {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: 80 }} />)}
                    </div>
                ) : packages.length === 0 ? (
                    <div className="empty-state" style={{ padding: '80px 20px' }}>
                        <Video />
                        <h3>No video packages yet</h3>
                        <p>Generate your first evidence-backed video script from your knowledge base</p>
                        <button className="btn btn-primary" onClick={() => setView('generate')} style={{ marginTop: 16 }}>
                            <Sparkles style={{ width: 16, height: 16 }} /> Generate First Package
                        </button>
                    </div>
                ) : (
                    <>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                            {packages.map(pkg => <PackageCard key={pkg.video_id} pkg={pkg} onClick={() => openDetail(pkg)} />)}
                        </div>
                        {nextCursor && (
                            <div style={{ textAlign: 'center', marginTop: 20 }}>
                                <button className="btn btn-secondary" onClick={() => loadList(nextCursor)} disabled={loadingList} style={{ fontSize: 13 }}>
                                    Load More
                                </button>
                            </div>
                        )}
                    </>
                )}
            </div>
        </>
    );

    /* ── GENERATE ── */
    if (view === 'generate') return (
        <>
            <div className="page-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <button className="btn btn-secondary" onClick={() => setView('list')} style={{ fontSize: 13, padding: '6px 12px' }}>
                        <ArrowLeft style={{ width: 14, height: 14 }} /> Back
                    </button>
                    <div>
                        <h2>Generate Video Package</h2>
                        <p>Create an evidence-backed script from your knowledge base</p>
                    </div>
                </div>
            </div>
            <div className="page-body">
                <div style={{ display: 'grid', gridTemplateColumns: '460px 1fr', gap: 24 }}>
                    <div className="card">
                        <h3 className="card-title" style={{ marginBottom: 20 }}>
                            <Sparkles style={{ width: 16, height: 16, display: 'inline', marginRight: 8, color: 'var(--accent-primary)' }} />
                            Package Settings
                        </h3>
                        <form onSubmit={handleGenerate}>
                            <div className="input-group">
                                <label>Topic *</label>
                                <textarea value={topic} onChange={e => setTopic(e.target.value)}
                                    placeholder="e.g., Benefits of vermicomposting for small-scale Indian farmers"
                                    required style={{ minHeight: 100 }} />
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                                <div className="input-group">
                                    <label>Format</label>
                                    <select value={format} onChange={e => setFormat(e.target.value)}>
                                        <option value="explainer">Explainer (5–8 min)</option>
                                        <option value="shorts">Shorts (60 sec)</option>
                                        <option value="deep_dive">Deep Dive (10+ min)</option>
                                    </select>
                                </div>
                                <div className="input-group">
                                    <label>Tone</label>
                                    <select value={tone} onChange={e => setTone(e.target.value)}>
                                        <option value="teacher">Teacher</option>
                                        <option value="storyteller">Storyteller</option>
                                        <option value="documentary">Documentary</option>
                                        <option value="conversational">Conversational</option>
                                    </select>
                                </div>
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                                <div className="input-group">
                                    <label>Audience</label>
                                    <select value={audience} onChange={e => setAudience(e.target.value)}>
                                        <option value="beginner">Beginner</option>
                                        <option value="intermediate">Intermediate</option>
                                        <option value="expert">Expert</option>
                                    </select>
                                </div>
                                <div className="input-group">
                                    <label>Language</label>
                                    <select value={langMode} onChange={e => setLangMode(e.target.value)}>
                                        <option value="en">English</option>
                                        <option value="mr">Marathi</option>
                                        <option value="hi">Hindi</option>
                                        <option value="hinglish">Hinglish</option>
                                        <option value="mr_plus_en_terms">Marathi + EN Terms</option>
                                    </select>
                                </div>
                            </div>
                            <button type="submit" className="btn btn-primary" disabled={generating} style={{ width: '100%', marginTop: 8 }}>
                                {generating
                                    ? <><Loader style={{ width: 16, height: 16, animation: 'spin 1s linear infinite' }} /> Generating…</>
                                    : <><Sparkles style={{ width: 16, height: 16 }} /> Generate Video Package</>
                                }
                            </button>
                        </form>
                    </div>
                    <div className="card">
                        {generating ? (
                            <div className="empty-state" style={{ padding: '80px 20px' }}>
                                <Loader style={{ width: 40, height: 40, animation: 'spin 1s linear infinite', color: 'var(--accent-primary)' }} />
                                <h3>Generating your package…</h3>
                                <p>Embedding topic → searching knowledge base → building script with evidence</p>
                            </div>
                        ) : (
                            <div className="empty-state" style={{ padding: '80px 20px' }}>
                                <Video />
                                <h3>Ready to generate</h3>
                                <p>Your package will include a scene-by-scene script, storyboard, visual specs, and clickable source evidence</p>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </>
    );

    /* ── DETAIL ── */
    return (
        <>
            <div className="page-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <button className="btn btn-secondary" onClick={() => { setView('list'); setDetail(null); }}
                        style={{ fontSize: 13, padding: '6px 12px' }}>
                        <ArrowLeft style={{ width: 14, height: 14 }} /> All Packages
                    </button>
                    {detail && (
                        <div>
                            <h2 style={{ fontSize: 18, marginBottom: 2 }}>{detail.topic}</h2>
                            <div style={{ display: 'flex', gap: 12, fontSize: 12, color: 'var(--text-muted)' }}>
                                <span><Film style={{ width: 11, height: 11, display: 'inline', marginRight: 3 }} />{detail.format}</span>
                                <span><Clock style={{ width: 11, height: 11, display: 'inline', marginRight: 3 }} />{Math.floor(totalDuration / 60)}:{String(totalDuration % 60).padStart(2, '0')}</span>
                                <span><Layers style={{ width: 11, height: 11, display: 'inline', marginRight: 3 }} />{detail.storyboard.scenes.length} scenes</span>
                                <span><BarChart3 style={{ width: 11, height: 11, display: 'inline', marginRight: 3 }} />{Math.round((detail.citations_report?.citation_coverage || 0) * 100)}% cited</span>
                            </div>
                        </div>
                    )}
                </div>
                {detail && (
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary" onClick={handleExport} style={{ fontSize: 13 }}>
                            <Download style={{ width: 14, height: 14 }} /> Export JSON
                        </button>
                        <button className="btn btn-primary" onClick={() => setView('generate')} style={{ fontSize: 13 }}>
                            <Plus style={{ width: 14, height: 14 }} /> New Package
                        </button>
                    </div>
                )}
            </div>

            <div className="page-body">
                {loadingDetail ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                        <div className="skeleton" style={{ height: 48 }} />
                        <div className="skeleton" style={{ height: 400 }} />
                    </div>
                ) : !detail ? null : (
                    <>
                        {detail.warnings?.length > 0 && (
                            <div style={{
                                padding: '10px 16px', marginBottom: 16, borderRadius: 'var(--radius-sm)',
                                background: 'rgba(253, 203, 110, 0.08)', border: '1px solid rgba(253, 203, 110, 0.2)',
                                fontSize: 13, color: 'var(--accent-warning)', display: 'flex', gap: 8, alignItems: 'flex-start',
                            }}>
                                <AlertTriangle style={{ width: 14, height: 14, marginTop: 2, flexShrink: 0 }} />
                                <div>{detail.warnings.join(' • ')}</div>
                            </div>
                        )}

                        {/* Tabs */}
                        <div style={{ display: 'flex', gap: 0, marginBottom: 20, borderBottom: '1px solid var(--border-subtle)' }}>
                            {([
                                { key: 'script' as Tab, label: 'Script', icon: FileText },
                                { key: 'storyboard' as Tab, label: 'Storyboard', icon: Film },
                                { key: 'visuals' as Tab, label: 'Visual Spec', icon: Eye },
                                { key: 'citations' as Tab, label: 'Citations', icon: BookOpen },
                            ]).map(t => (
                                <button key={t.key} onClick={() => setActiveTab(t.key)} style={{
                                    padding: '10px 20px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
                                    background: 'transparent', border: 'none',
                                    color: activeTab === t.key ? 'var(--accent-primary)' : 'var(--text-muted)',
                                    borderBottom: activeTab === t.key ? '2px solid var(--accent-primary)' : '2px solid transparent',
                                    display: 'flex', alignItems: 'center', gap: 6, transition: 'var(--transition-fast)',
                                }}>
                                    <t.icon style={{ width: 14, height: 14 }} /> {t.label}
                                </button>
                            ))}
                        </div>

                        {/* Script tab */}
                        {activeTab === 'script' && (
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', gap: 20 }}>
                                <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                                    <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border-subtle)' }}>
                                        <h4 style={{ fontSize: 14, fontWeight: 600 }}>Script — click a scene to see evidence</h4>
                                    </div>
                                    <div style={{ maxHeight: 600, overflow: 'auto' }}>
                                        {detail.evidence_map.paragraphs.map(para => (
                                            <div key={para.paragraph_id} onClick={() => setSelectedScene(para.scene_number)} style={{
                                                padding: '14px 20px', cursor: 'pointer',
                                                borderBottom: '1px solid var(--border-subtle)',
                                                background: selectedScene === para.scene_number ? 'rgba(108, 92, 231, 0.1)' : 'transparent',
                                                borderLeft: selectedScene === para.scene_number ? '3px solid var(--accent-primary)' : '3px solid transparent',
                                                transition: 'var(--transition-fast)',
                                            }}>
                                                <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--accent-primary)', marginBottom: 6, letterSpacing: '0.5px' }}>
                                                    Scene {para.scene_number}
                                                    {para.evidence_refs.length > 0
                                                        ? <span style={{ marginLeft: 8, color: 'var(--accent-success)', fontWeight: 500, textTransform: 'none' }}>✓ {para.evidence_refs.length} source{para.evidence_refs.length > 1 ? 's' : ''}</span>
                                                        : <span style={{ marginLeft: 8, color: 'var(--accent-warning)', fontWeight: 500, textTransform: 'none' }}>⚠ unsourced</span>
                                                    }
                                                </div>
                                                <p style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text-secondary)' }}>{para.script_text}</p>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                                <div className="card" style={{ padding: 0, overflow: 'hidden', position: 'sticky', top: 20, alignSelf: 'start' }}>
                                    <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border-subtle)' }}>
                                        <h4 style={{ fontSize: 14, fontWeight: 600 }}>
                                            <BookOpen style={{ width: 14, height: 14, display: 'inline', marginRight: 6 }} />Evidence
                                        </h4>
                                    </div>
                                    <div style={{ maxHeight: 540, overflow: 'auto', padding: '12px 0' }}>
                                        {selectedScene ? (() => {
                                            const para = evidenceForScene(selectedScene);
                                            if (!para || para.evidence_refs.length === 0) return (
                                                <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
                                                    <AlertTriangle style={{ width: 20, height: 20, margin: '0 auto 8px', display: 'block', color: 'var(--accent-warning)' }} />
                                                    No evidence — interpretation or CTA scene.
                                                </div>
                                            );
                                            return para.evidence_refs.map((ref, i) => (
                                                <div key={i} style={{ padding: '12px 20px', borderBottom: '1px solid var(--border-subtle)' }}>
                                                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, display: 'flex', justifyContent: 'space-between' }}>
                                                        <span>📖 {ref.book_title || 'Source'}</span>
                                                        <span>p. {ref.page_start}–{ref.page_end}</span>
                                                    </div>
                                                    <p style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--text-secondary)', background: 'var(--bg-glass)', padding: '8px 10px', borderRadius: 'var(--radius-sm)', borderLeft: '2px solid var(--accent-secondary)' }}>
                                                        {ref.snippet}
                                                    </p>
                                                </div>
                                            ));
                                        })() : (
                                            <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
                                                Click a scene on the left to view its source evidence
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Storyboard tab */}
                        {activeTab === 'storyboard' && (
                            <div style={{ display: 'grid', gap: 16 }}>
                                {detail.storyboard.scenes.map(scene => <SceneCard key={scene.scene_number} scene={scene} />)}
                            </div>
                        )}

                        {/* Visuals tab */}
                        {activeTab === 'visuals' && (
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                                {detail.visual_spec.diagrams.map((d, i) => (
                                    <div key={i} className="card">
                                        <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--accent-secondary)', marginBottom: 8 }}>{d.type.replace(/_/g, ' ')}</div>
                                        <h4 style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>{d.title}</h4>
                                        <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, marginBottom: 12 }}>{d.description}</p>
                                        {d.mermaid_hint && <pre style={{ padding: 12, borderRadius: 'var(--radius-sm)', background: 'var(--bg-glass)', fontSize: 11, color: 'var(--text-muted)', overflow: 'auto' }}>{d.mermaid_hint}</pre>}
                                    </div>
                                ))}
                                {detail.visual_spec.icon_suggestions.length > 0 && (
                                    <div className="card">
                                        <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Suggested Icons</h4>
                                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                                            {detail.visual_spec.icon_suggestions.map((icon, i) => <span key={i} className="badge">{icon}</span>)}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Citations tab */}
                        {activeTab === 'citations' && (
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                                <div className="card">
                                    <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>Citation Coverage</h4>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
                                        <StatBlock label="Coverage" value={`${Math.round((detail.citations_report.citation_coverage || 0) * 100)}%`}
                                            color={detail.citations_report.citation_coverage >= 0.9 ? 'var(--accent-success)' : 'var(--accent-warning)'} />
                                        <StatBlock label="Supported Scenes" value={`${detail.citations_report.supported_scenes}/${detail.citations_report.total_scenes}`} color="var(--accent-primary)" />
                                        <StatBlock label="Unique Chunks" value={String(detail.citations_report.cited_chunk_ids?.length || 0)} color="var(--accent-secondary)" />
                                    </div>
                                </div>
                                <div className="card">
                                    <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>Source Books</h4>
                                    {detail.citations_report.books.map((book, i) => (
                                        <div key={i} style={{ padding: '10px 0', borderBottom: '1px solid var(--border-subtle)', fontSize: 13 }}>
                                            <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{book.book_title || `Book ${book.book_id.slice(0, 8)}…`}</div>
                                            <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 4 }}>
                                                {book.cited_chunk_count} chunks • Pages: {book.pages_cited.slice(0, 10).join(', ')}
                                                {book.pages_cited.length > 10 && ` +${book.pages_cited.length - 10} more`}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </>
                )}
            </div>
        </>
    );
}

/* ── Sub-components ──────────────────────────────────────────────────────────── */

function PackageCard({ pkg, onClick }: { pkg: VideoPackage; onClick: () => void }) {
    const date = new Date(pkg.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
    const coverage = Math.round((pkg.citation_coverage || 0) * 100);
    return (
        <div className="card" onClick={onClick} style={{
            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            gap: 16, padding: '16px 20px', transition: 'var(--transition-fast)',
        }}
            onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--accent-primary)')}
            onMouseLeave={e => (e.currentTarget.style.borderColor = '')}
        >
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, minWidth: 0 }}>
                <div style={{
                    width: 40, height: 40, borderRadius: 'var(--radius-sm)', flexShrink: 0,
                    background: 'rgba(108, 92, 231, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                    <Film style={{ width: 18, height: 18, color: 'var(--accent-primary)' }} />
                </div>
                <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {pkg.topic}
                    </div>
                    <div style={{ display: 'flex', gap: 10, marginTop: 4, fontSize: 12, color: 'var(--text-muted)', flexWrap: 'wrap' }}>
                        <span>{pkg.format}</span>
                        <span>{pkg.tone}</span>
                        <span>{pkg.language_mode}</span>
                        <span>{pkg.scene_count} scenes</span>
                        <span>{date}</span>
                    </div>
                </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0 }}>
                <div style={{ textAlign: 'center' }}>
                    <div style={{
                        fontSize: 18, fontWeight: 800, lineHeight: 1,
                        color: coverage >= 90 ? 'var(--accent-success)' : coverage >= 70 ? 'var(--accent-warning)' : 'var(--accent-danger)',
                    }}>{coverage}%</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>cited</div>
                </div>
                <span className="badge">v{pkg.version}</span>
            </div>
        </div>
    );
}

function StatBlock({ label, value, color }: { label: string; value: string; color: string }) {
    return (
        <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 800, color, lineHeight: 1 }}>{value}</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>{label}</div>
        </div>
    );
}

function SceneCard({ scene }: { scene: VideoPackageFull['storyboard']['scenes'][0] }) {
    const [expanded, setExpanded] = useState(false);
    const words = scene.voiceover.split(/\s+/).length;
    const paceOk = words >= scene.duration_sec * 2;
    return (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div onClick={() => setExpanded(!expanded)} style={{
                padding: '16px 20px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between',
                alignItems: 'center', borderBottom: expanded ? '1px solid var(--border-subtle)' : 'none',
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span style={{
                        width: 28, height: 28, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 12, fontWeight: 700,
                        background: scene.is_interpretation ? 'rgba(253, 203, 110, 0.15)' : 'rgba(108, 92, 231, 0.15)',
                        color: scene.is_interpretation ? 'var(--accent-warning)' : 'var(--accent-primary)',
                    }}>{scene.scene_number}</span>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{scene.title}</div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', gap: 10, marginTop: 2 }}>
                            <span>{scene.duration_sec}s</span>
                            <span>{words} words</span>
                            {!paceOk && <span style={{ color: 'var(--accent-warning)' }}>⚠ thin voiceover</span>}
                        </div>
                    </div>
                </div>
                {expanded ? <ChevronUp style={{ width: 16, height: 16, color: 'var(--text-muted)' }} /> : <ChevronDown style={{ width: 16, height: 16, color: 'var(--text-muted)' }} />}
            </div>
            {expanded && (
                <div style={{ padding: '16px 20px' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
                        <div>
                            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--accent-primary)', marginBottom: 6, letterSpacing: '0.5px' }}>Voiceover</div>
                            <p style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text-secondary)' }}>{scene.voiceover}</p>
                        </div>
                        <div>
                            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--accent-secondary)', marginBottom: 6, letterSpacing: '0.5px' }}>Visual Direction</div>
                            <p style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text-secondary)', marginBottom: 12 }}>{scene.visual_description}</p>
                            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--accent-tertiary)', marginBottom: 6, letterSpacing: '0.5px' }}>On-Screen Text</div>
                            <div style={{ padding: '6px 12px', borderRadius: 'var(--radius-sm)', background: 'var(--bg-glass)', fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', display: 'inline-block' }}>
                                {scene.on_screen_text}
                            </div>
                        </div>
                    </div>
                    {scene.animation_cues.length > 0 && (
                        <div>
                            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6, letterSpacing: '0.5px' }}>Animation Cues</div>
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                {scene.animation_cues.map((cue, i) => <span key={i} className="badge">{cue}</span>)}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
