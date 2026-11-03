import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ApiError, getToken, NetworkError, setAuthExpiredHandler, setToken } from "./api";
import type { User } from "./types";

const LAST_USER_KEY = "mp.user";

interface AuthState {
  user: User | null;
  loading: boolean;
  expired: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(() => getToken() !== null);
  const [expired, setExpired] = useState(false);

  const clear = useCallback(() => {
    setToken(null);
    localStorage.removeItem(LAST_USER_KEY);
    setUser(null);
  }, []);

  useEffect(() => {
    setAuthExpiredHandler(() => {
      clear();
      setExpired(true);
    });
  }, [clear]);

  useEffect(() => {
    if (!getToken()) return;
    api<User>("/auth/me")
      .then((me) => {
        localStorage.setItem(LAST_USER_KEY, JSON.stringify(me));
        setUser(me);
      })
      .catch((error) => {
        // Sem rede: segue com o último usuário, para a pessoa continuar contando no aparelho.
        const saved = localStorage.getItem(LAST_USER_KEY);
        if (error instanceof NetworkError && saved) setUser(JSON.parse(saved) as User);
        else if (!(error instanceof ApiError && error.status === 401)) clear();
      })
      .finally(() => setLoading(false));
  }, [clear]);

  const value = useMemo<AuthState>(() => ({
    user,
    loading,
    expired,
    login: async (email, password) => {
      const { access_token } = await api<{ access_token: string }>("/auth/login", {
        method: "POST", body: { email, password }, anonymous: true,
      });
      setToken(access_token);
      const me = await api<User>("/auth/me");
      localStorage.setItem(LAST_USER_KEY, JSON.stringify(me));
      setExpired(false);
      setUser(me);
    },
    logout: () => {
      setExpired(false);
      clear();
    },
  }), [user, loading, expired, clear]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth fora do AuthProvider");
  return ctx;
}

export function useUser(): User {
  const { user } = useAuth();
  if (!user) throw new Error("Rota protegida sem usuário");
  return user;
}

export const canManage = (u: User) => u.role === "admin" || u.role === "manager";
export const canCount = (u: User) => u.role !== "auditor";
export const canReview = (u: User) => u.role !== "operator";
