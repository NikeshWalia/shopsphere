/**
 * Authentication state.
 *
 * The token lives in localStorage, which is a deliberate and documented
 * trade-off: it is readable by any script on the page, so a successful XSS
 * could steal it. An httpOnly cookie would not be, but would need CSRF
 * protection and a same-site deployment. For a demo whose point is a testable
 * REST API consumed by a SPA, bearer-token-in-storage is the honest choice -
 * see "Architecture decisions" in the README.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { api, setUnauthorizedHandler, tokenStore } from '@/api/client';
import type { User } from '@/types/api';

interface AuthContextValue {
  user: User | null;
  /** True until the stored token has been validated against the API. */
  initialising: boolean;
  isAuthenticated: boolean;
  isAdmin: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (payload: {
    email: string;
    password: string;
    password_confirm: string;
    full_name: string;
    phone?: string;
  }) => Promise<User>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [initialising, setInitialising] = useState(true);

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  const refresh = useCallback(async () => {
    if (!tokenStore.get()) {
      setUser(null);
      return;
    }
    try {
      setUser(await api.auth.me());
    } catch {
      // The token is present but no longer usable. The client has already
      // cleared it; drop the user so the UI reflects being signed out.
      setUser(null);
    }
  }, []);

  // A stored token is validated on boot rather than trusted, so a revoked or
  // expired token never produces a UI that looks logged in but cannot act.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await refresh();
      if (!cancelled) setInitialising(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // Any 401 from any request signs the user out, so one expired token cannot
  // leave the app in a half-authenticated state.
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const response = await api.auth.login(email, password);
    tokenStore.set(response.access_token);
    setUser(response.user);
    return response.user;
  }, []);

  const register = useCallback<AuthContextValue['register']>(async (payload) => {
    const response = await api.auth.register(payload);
    tokenStore.set(response.access_token);
    setUser(response.user);
    return response.user;
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      initialising,
      isAuthenticated: user !== null,
      isAdmin: user?.role === 'admin',
      login,
      register,
      logout,
      refresh,
    }),
    [user, initialising, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside an AuthProvider');
  return context;
}
