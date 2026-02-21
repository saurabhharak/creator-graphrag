import { create } from 'zustand';
import { authApi } from '../api/client';

interface User {
    user_id: string;
    email: string;
    display_name: string;
    role: string;
}

interface AuthState {
    user: User | null;
    isLoading: boolean;
    isAuthenticated: boolean;

    login: (email: string, password: string) => Promise<void>;
    register: (email: string, password: string, displayName: string) => Promise<void>;
    logout: () => void;
    loadUser: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
    user: null,
    isLoading: true,
    isAuthenticated: false,

    login: async (email, password) => {
        const res = await authApi.login(email, password);
        localStorage.setItem('access_token', res.access_token);
        localStorage.setItem('refresh_token', res.refresh_token);
        const user = await authApi.me();
        set({ user, isAuthenticated: true, isLoading: false });
    },

    register: async (email, password, displayName) => {
        const res = await authApi.register(email, password, displayName);
        localStorage.setItem('access_token', res.access_token);
        localStorage.setItem('refresh_token', res.refresh_token);
        const user = await authApi.me();
        set({ user, isAuthenticated: true, isLoading: false });
    },

    logout: () => {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        set({ user: null, isAuthenticated: false, isLoading: false });
    },

    loadUser: async () => {
        const token = localStorage.getItem('access_token');
        if (!token) {
            set({ isLoading: false });
            return;
        }
        try {
            const user = await authApi.me();
            set({ user, isAuthenticated: true, isLoading: false });
        } catch {
            localStorage.removeItem('access_token');
            localStorage.removeItem('refresh_token');
            set({ isLoading: false });
        }
    },
}));
