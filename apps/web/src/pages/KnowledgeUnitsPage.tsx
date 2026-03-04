import { useEffect, useState } from 'react';
import { kuApi, type KnowledgeUnit } from '../api/client';
import { BrainCircuit, Check, X, Search } from 'lucide-react';
import toast from 'react-hot-toast';

export default function KnowledgeUnitsPage() {
    const [units, setUnits] = useState<KnowledgeUnit[]>([]);
    const [loading, setLoading] = useState(true);
    const [statusFilter, setStatusFilter] = useState('');
    const [searchQ, setSearchQ] = useState('');
    const [selected, setSelected] = useState<Set<string>>(new Set());

    const loadUnits = async () => {
        setLoading(true);
        try {
            const res = await kuApi.list({ status: statusFilter || undefined, limit: 50 });
            setUnits(res.items);
        } catch {
            toast.error('Failed to load knowledge units');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { loadUnits(); }, [statusFilter]);

    const toggleSelect = (id: string) => {
        const next = new Set(selected);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        setSelected(next);
    };

    const toggleAll = () => {
        if (selected.size === filtered.length) {
            setSelected(new Set());
        } else {
            setSelected(new Set(filtered.map((u) => u.unit_id)));
        }
    };

    const handleBulkAction = async (action: 'approve' | 'reject') => {
        if (selected.size === 0) return;
        try {
            const res = await kuApi.bulkUpdate(Array.from(selected), action);
            toast.success(`${res.succeeded} units ${action}d`);
            setSelected(new Set());
            loadUnits();
        } catch {
            toast.error(`Failed to ${action} units`);
        }
    };

    const handleSingleAction = async (id: string, action: 'approved' | 'rejected') => {
        try {
            await kuApi.update(id, { status: action });
            toast.success(`Unit ${action}`);
            loadUnits();
        } catch {
            toast.error(`Failed to update unit`);
        }
    };

    const filtered = units.filter(
        (u) =>
            (u.subject || '').toLowerCase().includes(searchQ.toLowerCase()) ||
            (u.object || '').toLowerCase().includes(searchQ.toLowerCase()) ||
            (u.predicate || '').toLowerCase().includes(searchQ.toLowerCase())
    );

    const statusCounts = {
        all: units.length,
        needs_review: units.filter((u) => u.status === 'needs_review').length,
        approved: units.filter((u) => u.status === 'approved').length,
        rejected: units.filter((u) => u.status === 'rejected').length,
    };

    return (
        <>
            <div className="page-header">
                <h2>Knowledge Units</h2>
                <p>Review and approve extracted knowledge triples</p>
            </div>

            <div className="page-body">
                {/* Status Tabs */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
                    {[
                        { key: '', label: 'All', count: statusCounts.all },
                        { key: 'needs_review', label: 'Needs Review', count: statusCounts.needs_review },
                        { key: 'approved', label: 'Approved', count: statusCounts.approved },
                        { key: 'rejected', label: 'Rejected', count: statusCounts.rejected },
                    ].map((tab) => (
                        <button
                            key={tab.key}
                            className={`btn btn-sm ${statusFilter === tab.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setStatusFilter(tab.key)}
                        >
                            {tab.label}
                            <span
                                style={{
                                    background: 'rgba(255,255,255,0.15)',
                                    padding: '1px 8px',
                                    borderRadius: 100,
                                    fontSize: 11,
                                    marginLeft: 4,
                                }}
                            >
                                {tab.count}
                            </span>
                        </button>
                    ))}
                </div>

                <div className="toolbar">
                    <div className="search-input">
                        <Search />
                        <input
                            placeholder="Search subject, predicate, or object..."
                            value={searchQ}
                            onChange={(e) => setSearchQ(e.target.value)}
                        />
                    </div>

                    <div className="toolbar-spacer" />

                    {selected.size > 0 && (
                        <>
                            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                                {selected.size} selected
                            </span>
                            <button className="btn btn-success btn-sm" onClick={() => handleBulkAction('approve')}>
                                <Check style={{ width: 14, height: 14 }} /> Approve
                            </button>
                            <button className="btn btn-danger btn-sm" onClick={() => handleBulkAction('reject')}>
                                <X style={{ width: 14, height: 14 }} /> Reject
                            </button>
                        </>
                    )}
                </div>

                {loading ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {[1, 2, 3, 4, 5].map((i) => (
                            <div key={i} className="skeleton" style={{ height: 56 }} />
                        ))}
                    </div>
                ) : filtered.length === 0 ? (
                    <div className="empty-state">
                        <BrainCircuit />
                        <h3>No knowledge units found</h3>
                        <p>Extract knowledge from your books to see units here</p>
                    </div>
                ) : (
                    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th style={{ width: 40 }}>
                                        <input
                                            type="checkbox"
                                            checked={selected.size === filtered.length && filtered.length > 0}
                                            onChange={toggleAll}
                                            style={{ cursor: 'pointer' }}
                                        />
                                    </th>
                                    <th>Subject</th>
                                    <th>Predicate</th>
                                    <th>Object</th>
                                    <th>Lang</th>
                                    <th>Confidence</th>
                                    <th>Status</th>
                                    <th style={{ width: 100 }}>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filtered.map((ku) => (
                                    <tr key={ku.unit_id}>
                                        <td>
                                            <input
                                                type="checkbox"
                                                checked={selected.has(ku.unit_id)}
                                                onChange={() => toggleSelect(ku.unit_id)}
                                                style={{ cursor: 'pointer' }}
                                            />
                                        </td>
                                        <td style={{ fontWeight: 500, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {ku.subject || '—'}
                                        </td>
                                        <td style={{ color: 'var(--text-accent)', fontSize: 13 }}>
                                            {ku.predicate || '—'}
                                        </td>
                                        <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {ku.object || '—'}
                                        </td>
                                        <td>
                                            <span className="badge badge-neutral">{ku.language_detected.toUpperCase()}</span>
                                        </td>
                                        <td>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                                <div
                                                    style={{
                                                        width: 48,
                                                        height: 4,
                                                        borderRadius: 2,
                                                        background: 'var(--bg-glass)',
                                                        overflow: 'hidden',
                                                    }}
                                                >
                                                    <div
                                                        style={{
                                                            width: `${ku.confidence * 100}%`,
                                                            height: '100%',
                                                            background:
                                                                ku.confidence >= 0.8
                                                                    ? 'var(--accent-success)'
                                                                    : ku.confidence >= 0.65
                                                                        ? 'var(--accent-warning)'
                                                                        : 'var(--accent-error)',
                                                            borderRadius: 2,
                                                        }}
                                                    />
                                                </div>
                                                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                                                    {(ku.confidence * 100).toFixed(0)}%
                                                </span>
                                            </div>
                                        </td>
                                        <td>
                                            <span
                                                className={`badge ${ku.status === 'approved'
                                                        ? 'badge-success'
                                                        : ku.status === 'rejected'
                                                            ? 'badge-error'
                                                            : 'badge-warning'
                                                    }`}
                                            >
                                                {ku.status.replace('_', ' ')}
                                            </span>
                                        </td>
                                        <td>
                                            <div style={{ display: 'flex', gap: 4 }}>
                                                {ku.status !== 'approved' && (
                                                    <button
                                                        className="btn btn-ghost btn-sm"
                                                        onClick={() => handleSingleAction(ku.unit_id, 'approved')}
                                                        title="Approve"
                                                    >
                                                        <Check style={{ width: 14, height: 14, color: 'var(--accent-success)' }} />
                                                    </button>
                                                )}
                                                {ku.status !== 'rejected' && (
                                                    <button
                                                        className="btn btn-ghost btn-sm"
                                                        onClick={() => handleSingleAction(ku.unit_id, 'rejected')}
                                                        title="Reject"
                                                    >
                                                        <X style={{ width: 14, height: 14, color: 'var(--accent-error)' }} />
                                                    </button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </>
    );
}
