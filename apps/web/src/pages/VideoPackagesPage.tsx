import { useState } from 'react';
import { videoApi } from '../api/client';
import { Video, Sparkles, Loader } from 'lucide-react';
import toast from 'react-hot-toast';

export default function VideoPackagesPage() {
    const [topic, setTopic] = useState('');
    const [format, setFormat] = useState('explainer');
    const [audience, setAudience] = useState('beginner');
    const [langMode, setLangMode] = useState('en');
    const [generating, setGenerating] = useState(false);
    const [result, setResult] = useState<unknown | null>(null);

    const handleGenerate = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!topic.trim()) return;
        setGenerating(true);
        setResult(null);
        try {
            const res = await videoApi.generate({ topic, format, audience_level: audience, language_mode: langMode });
            setResult(res);
            toast.success('Video package generated!');
        } catch (err) {
            toast.error(err instanceof Error ? err.message : 'Generation failed');
        } finally {
            setGenerating(false);
        }
    };

    return (
        <>
            <div className="page-header">
                <h2>Video Packages</h2>
                <p>Generate evidence-backed video scripts from your knowledge base</p>
            </div>

            <div className="page-body">
                <div style={{ display: 'grid', gridTemplateColumns: '400px 1fr', gap: 24 }}>
                    {/* Generator Form */}
                    <div className="card">
                        <h3 className="card-title" style={{ marginBottom: 20 }}>
                            <Sparkles style={{ width: 16, height: 16, display: 'inline', marginRight: 8, color: 'var(--accent-primary)' }} />
                            Generate Package
                        </h3>

                        <form onSubmit={handleGenerate}>
                            <div className="input-group">
                                <label>Topic *</label>
                                <textarea
                                    value={topic}
                                    onChange={(e) => setTopic(e.target.value)}
                                    placeholder="e.g., Benefits of vermicomposting for small-scale Indian farmers"
                                    required
                                    style={{ minHeight: 100 }}
                                />
                            </div>

                            <div className="input-group">
                                <label>Format</label>
                                <select value={format} onChange={(e) => setFormat(e.target.value)}>
                                    <option value="explainer">Explainer (5-8 min)</option>
                                    <option value="shorts">Shorts (60 sec)</option>
                                    <option value="deep_dive">Deep Dive (10+ min)</option>
                                </select>
                            </div>

                            <div className="input-group">
                                <label>Audience Level</label>
                                <select value={audience} onChange={(e) => setAudience(e.target.value)}>
                                    <option value="beginner">Beginner</option>
                                    <option value="intermediate">Intermediate</option>
                                </select>
                            </div>

                            <div className="input-group">
                                <label>Language</label>
                                <select value={langMode} onChange={(e) => setLangMode(e.target.value)}>
                                    <option value="en">English</option>
                                    <option value="mr">Marathi</option>
                                    <option value="hi">Hindi</option>
                                    <option value="hinglish">Hinglish (Hindi + English)</option>
                                    <option value="mr_plus_en_terms">Marathi + English Technical Terms</option>
                                </select>
                            </div>

                            <button type="submit" className="btn btn-primary" disabled={generating} style={{ width: '100%' }}>
                                {generating ? (
                                    <>
                                        <Loader style={{ width: 16, height: 16, animation: 'spin 1s linear infinite' }} />
                                        Generating...
                                    </>
                                ) : (
                                    <>
                                        <Sparkles style={{ width: 16, height: 16 }} />
                                        Generate Video Package
                                    </>
                                )}
                            </button>
                        </form>
                    </div>

                    {/* Result Pane */}
                    <div className="card">
                        {!result ? (
                            <div className="empty-state" style={{ padding: '80px 20px' }}>
                                <Video />
                                <h3>Ready to generate</h3>
                                <p>
                                    Fill in the form and click Generate to create a video package with
                                    evidence-backed script, storyboard, and visual specifications
                                </p>
                            </div>
                        ) : (
                            <div>
                                <h3 className="card-title" style={{ marginBottom: 16 }}>Generated Package</h3>
                                <pre
                                    style={{
                                        padding: 20,
                                        borderRadius: 'var(--radius-sm)',
                                        background: 'var(--bg-glass)',
                                        fontSize: 13,
                                        color: 'var(--text-secondary)',
                                        overflow: 'auto',
                                        maxHeight: 600,
                                        lineHeight: 1.7,
                                        whiteSpace: 'pre-wrap',
                                    }}
                                >
                                    {JSON.stringify(result, null, 2)}
                                </pre>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </>
    );
}
