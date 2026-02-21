import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';
import { LogIn, UserPlus, AlertCircle } from 'lucide-react';

export default function LoginPage() {
    const [isRegister, setIsRegister] = useState(false);
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [displayName, setDisplayName] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const { login, register } = useAuthStore();
    const navigate = useNavigate();

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);

        try {
            if (isRegister) {
                await register(email, password, displayName);
            } else {
                await login(email, password);
            }
            navigate('/');
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : 'Something went wrong';
            setError(message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="login-page">
            <div className="login-card">
                <div style={{ textAlign: 'center', marginBottom: 8 }}>
                    <div
                        className="sidebar-brand-icon"
                        style={{ width: 48, height: 48, fontSize: 22, margin: '0 auto 16px' }}
                    >
                        C
                    </div>
                </div>

                <h1>Creator Studio</h1>
                <p>{isRegister ? 'Create your account' : 'Sign in to your workspace'}</p>

                {error && (
                    <div
                        style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                            padding: '10px 14px',
                            borderRadius: 'var(--radius-sm)',
                            background: 'rgba(225, 112, 85, 0.1)',
                            border: '1px solid rgba(225, 112, 85, 0.2)',
                            color: 'var(--accent-error)',
                            fontSize: 13,
                            marginBottom: 20,
                        }}
                    >
                        <AlertCircle style={{ width: 16, height: 16, flexShrink: 0 }} />
                        {error}
                    </div>
                )}

                <form onSubmit={handleSubmit}>
                    {isRegister && (
                        <div className="input-group">
                            <label>Display Name</label>
                            <input
                                type="text"
                                value={displayName}
                                onChange={(e) => setDisplayName(e.target.value)}
                                placeholder="Your name"
                                required
                            />
                        </div>
                    )}

                    <div className="input-group">
                        <label>Email</label>
                        <input
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            placeholder="you@example.com"
                            required
                        />
                    </div>

                    <div className="input-group">
                        <label>Password</label>
                        <input
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            placeholder={isRegister ? 'At least 12 characters' : 'Your password'}
                            required
                            minLength={isRegister ? 12 : 1}
                        />
                    </div>

                    <button type="submit" className="btn btn-primary" disabled={loading}>
                        {loading ? (
                            'Please wait...'
                        ) : isRegister ? (
                            <>
                                <UserPlus style={{ width: 16, height: 16 }} /> Create Account
                            </>
                        ) : (
                            <>
                                <LogIn style={{ width: 16, height: 16 }} /> Sign In
                            </>
                        )}
                    </button>
                </form>

                <div className="login-toggle">
                    {isRegister ? 'Already have an account? ' : "Don't have an account? "}
                    <button onClick={() => { setIsRegister(!isRegister); setError(''); }}>
                        {isRegister ? 'Sign in' : 'Create one'}
                    </button>
                </div>
            </div>
        </div>
    );
}
