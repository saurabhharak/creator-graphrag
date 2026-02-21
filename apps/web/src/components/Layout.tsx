import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import {
    BookOpen,
    LayoutDashboard,
    Search,
    BrainCircuit,
    Video,
    Network,
    LogOut,
    Shield,
} from 'lucide-react';
import { useAuthStore } from '../stores/authStore';

export default function Layout() {
    const { user, logout } = useAuthStore();
    const navigate = useNavigate();

    const handleLogout = () => {
        logout();
        navigate('/login');
    };

    return (
        <div className="app-layout">
            {/* Sidebar */}
            <aside className="sidebar">
                <div className="sidebar-brand">
                    <div className="sidebar-brand-icon">C</div>
                    <div>
                        <h1>Creator Studio</h1>
                        <span>GraphRAG Engine</span>
                    </div>
                </div>

                <nav className="sidebar-nav">
                    <div className="sidebar-section-label">Main</div>
                    <NavLink to="/" end className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                        <LayoutDashboard /> Dashboard
                    </NavLink>
                    <NavLink to="/books" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                        <BookOpen /> Library
                    </NavLink>
                    <NavLink to="/search" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                        <Search /> Search
                    </NavLink>

                    <div className="sidebar-section-label">Knowledge</div>
                    <NavLink to="/knowledge-units" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                        <BrainCircuit /> Knowledge Units
                    </NavLink>
                    <NavLink to="/graph" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                        <Network /> Knowledge Graph
                    </NavLink>

                    <div className="sidebar-section-label">Create</div>
                    <NavLink to="/video-packages" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                        <Video /> Video Packages
                    </NavLink>
                </nav>

                <div className="sidebar-footer">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                        <div
                            style={{
                                width: 32,
                                height: 32,
                                borderRadius: '50%',
                                background: 'var(--gradient-primary)',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                fontSize: 13,
                                fontWeight: 700,
                            }}
                        >
                            {user?.display_name?.charAt(0).toUpperCase() || 'U'}
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                {user?.display_name || 'User'}
                            </div>
                            <div style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
                                <Shield style={{ width: 10, height: 10 }} />
                                {user?.role || 'editor'}
                            </div>
                        </div>
                    </div>
                    <button className="nav-link" onClick={handleLogout} style={{ width: '100%' }}>
                        <LogOut /> Sign Out
                    </button>
                </div>
            </aside>

            {/* Main Content */}
            <main className="main-content">
                <Outlet />
            </main>
        </div>
    );
}
