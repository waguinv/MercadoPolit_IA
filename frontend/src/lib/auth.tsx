import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, NetworkError, setAccessToken, setAuthExpiredHandler } from "./api";
import type { User } from "./types";

const LAST_USER_KEY = "mp.user";

function savedUser(): User | null {
  try {
    const saved = localStorage.getItem(LAST_USER_KEY);
    return saved ? (JSON.parse(saved) as User) : null;
  } catch {
    return null;
  }
}

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
  // Sem usuário salvo, não tenta renovar: depois de "Sair" sem rede, o cookie antigo pode ter ficado no
  // aparelho, e a próxima pessoa não pode entrar com o login de quem saiu.
  const [loading, setLoading] = useState(() => savedUser() !== null);
  const [expired, setExpired] = useState(false);

  const clear = useCallback(() => {
    setAccessToken(null);
    localStorage.removeItem(LAST_USER_KEY);
    setUser(null);
  }, []);

  useEffect(() => {
    setAuthExpiredHandler(() => {
      clear();
      setExpired(true);
    });
    localStorage.removeItem("mp.token"); // versões antigas guardavam o token aqui
  }, [clear]);

  useEffect(() => {
    const saved = savedUser();
    if (!saved) return;
    api<User>("/auth/me")
      .then((me) => {
        localStorage.setItem(LAST_USER_KEY, JSON.stringify(me));
        setUser(me);
      })
      .catch((error) => {
        // Sem rede: segue com o último usuário, para a pessoa continuar contando no aparelho.
        if (error instanceof NetworkError) setUser(saved);
        else clear();
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
      setAccessToken(access_token);
      const me = await api<User>("/auth/me");
      localStorage.setItem(LAST_USER_KEY, JSON.stringify(me));
      setExpired(false);
      setUser(me);
    },
    logout: () => {
      setExpired(false);
      clear();
      // Sem rede o login no servidor só expira depois; neste aparelho a saída já vale (ver acima).
      void api("/auth/logout", { method: "POST", anonymous: true }).catch(() => undefined);
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
export const isAdmin = (u: User) => u.role === "admin";
