import { useEffect, useState } from 'react';
import { BookOpen, Plus, Search, Trash2, Globe, RefreshCw } from 'lucide-react';
import { booksApi, type Book } from '../api/client';
import toast from 'react-hot-toast';

export default function BooksPage() {
    const [books, setBooks] = useState<Book[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [langFilter, setLangFilter] = useState('');
    const [showCreate, setShowCreate] = useState(false);

    const loadBooks = async () => {
        setLoading(true);
        try {
            const res = await booksApi.list(undefined, langFilter || undefined);
            setBooks(res.items);
        } catch (err) {
            toast.error('Failed to load books');
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { loadBooks(); }, [langFilter]);

    const filteredBooks = books.filter(
        (b) => b.title.toLowerCase().includes(search.toLowerCase()) ||
            (b.author || '').toLowerCase().includes(search.toLowerCase())
    );

    const handleDelete = async (bookId: string) => {
        if (!confirm('Are you sure you want to delete this book?')) return;
        try {
            await booksApi.delete(bookId);
            toast.success('Book deleted');
            loadBooks();
        } catch {
            toast.error('Failed to delete book');
        }
    };

    return (
        <>
            <div className="page-header">
                <h2>Library</h2>
                <p>Manage your indexed books and ingestion pipeline</p>
            </div>

            <div className="page-body">
                <div className="toolbar">
                    <div className="search-input">
                        <Search />
                        <input
                            placeholder="Search books..."
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                    </div>

                    <select
                        value={langFilter}
                        onChange={(e) => setLangFilter(e.target.value)}
                        style={{ minWidth: 160, width: 'auto' }}
                    >
                        <option value="">All Languages</option>
                        <option value="en">English</option>
                        <option value="mr">Marathi</option>
                        <option value="hi">Hindi</option>
                        <option value="mixed">Mixed</option>
                    </select>

                    <div className="toolbar-spacer" />

                    <button className="btn btn-ghost btn-sm" onClick={loadBooks}>
                        <RefreshCw style={{ width: 14, height: 14 }} />
                    </button>

                    <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
                        <Plus style={{ width: 16, height: 16 }} /> Add Book
                    </button>
                </div>

                {loading ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                        {[1, 2, 3].map((i) => (
                            <div key={i} className="skeleton" style={{ height: 80 }} />
                        ))}
                    </div>
                ) : filteredBooks.length === 0 ? (
                    <div className="empty-state">
                        <BookOpen />
                        <h3>No books found</h3>
                        <p>Upload your first book to start building your knowledge base</p>
                        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
                            <Plus style={{ width: 16, height: 16 }} /> Upload Book
                        </button>
                    </div>
                ) : (
                    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Title</th>
                                    <th>Author</th>
                                    <th>Language</th>
                                    <th>Status</th>
                                    <th>Tags</th>
                                    <th>Created</th>
                                    <th style={{ width: 60 }} />
                                </tr>
                            </thead>
                            <tbody>
                                {filteredBooks.map((book) => (
                                    <tr key={book.book_id}>
                                        <td>
                                            <div style={{ fontWeight: 600 }}>{book.title}</div>
                                        </td>
                                        <td style={{ color: 'var(--text-secondary)' }}>{book.author || '—'}</td>
                                        <td>
                                            <span className="badge badge-info">
                                                <Globe style={{ width: 10, height: 10 }} />
                                                {book.language_primary.toUpperCase()}
                                            </span>
                                        </td>
                                        <td>
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
                                        </td>
                                        <td>
                                            {(book.tags || []).slice(0, 3).map((t) => (
                                                <span key={t} className="badge badge-neutral" style={{ marginRight: 4 }}>
                                                    {t}
                                                </span>
                                            ))}
                                        </td>
                                        <td style={{ color: 'var(--text-muted)', fontSize: 13 }}>
                                            {new Date(book.created_at).toLocaleDateString()}
                                        </td>
                                        <td>
                                            <button
                                                className="btn btn-ghost btn-sm"
                                                onClick={() => handleDelete(book.book_id)}
                                                title="Delete book"
                                            >
                                                <Trash2 style={{ width: 14, height: 14, color: 'var(--accent-error)' }} />
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Create Book Modal */}
            {showCreate && (
                <CreateBookModal onClose={() => setShowCreate(false)} onCreated={loadBooks} />
            )}
        </>
    );
}

function CreateBookModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
    const [title, setTitle] = useState('');
    const [author, setAuthor] = useState('');
    const [lang, setLang] = useState('en');
    const [tags, setTags] = useState('');
    const [creating, setCreating] = useState(false);

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        setCreating(true);
        try {
            await booksApi.create({
                title,
                language_primary: lang,
                author: author || undefined,
                tags: tags ? tags.split(',').map((t) => t.trim()) : [],
            });
            toast.success('Book created!');
            onCreated();
            onClose();
        } catch (err) {
            toast.error(err instanceof Error ? err.message : 'Failed to create book');
        } finally {
            setCreating(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3>Add New Book</h3>
                    <button className="btn btn-ghost btn-sm" onClick={onClose}>✕</button>
                </div>

                <form onSubmit={handleCreate}>
                    <div className="modal-body">
                        <div className="input-group">
                            <label>Book Title *</label>
                            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} required />
                        </div>
                        <div className="input-group">
                            <label>Author</label>
                            <input type="text" value={author} onChange={(e) => setAuthor(e.target.value)} />
                        </div>
                        <div className="input-group">
                            <label>Primary Language *</label>
                            <select value={lang} onChange={(e) => setLang(e.target.value)}>
                                <option value="en">English</option>
                                <option value="mr">Marathi</option>
                                <option value="hi">Hindi</option>
                                <option value="mixed">Mixed</option>
                            </select>
                        </div>
                        <div className="input-group">
                            <label>Tags (comma separated)</label>
                            <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="farming, organic" />
                        </div>
                    </div>

                    <div className="modal-footer">
                        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                        <button type="submit" className="btn btn-primary" disabled={creating}>
                            {creating ? 'Creating...' : 'Create Book'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
