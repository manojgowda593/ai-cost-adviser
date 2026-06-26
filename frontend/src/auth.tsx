// Auth context: holds the current user + token, persists token to localStorage,
// and exposes login/signup/logout. Components use `useAuth()`.

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, clearToken, getToken, setToken, type User } from "./api";

interface AuthState {
  user: User | null;
  token: string | null;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string) => Promise<void>;
  logout: () => void;
  isAuthenticated: boolean;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

const USER_KEY = "aca_user";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTok] = useState<string | null>(() => getToken());
  const [user, setUser] = useState<User | null>(() => {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as User) : null;
  });

  useEffect(() => {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
    else localStorage.removeItem(USER_KEY);
  }, [user]);

  async function login(email: string, password: string) {
    const res = await api.login(email, password);
    setToken(res.token);
    setTok(res.token);
    setUser(res.user);
  }

  async function signup(email: string, password: string) {
    const res = await api.signup(email, password);
    setToken(res.token);
    setTok(res.token);
    setUser(res.user);
  }

  function logout() {
    clearToken();
    setTok(null);
    setUser(null);
  }

  const value = useMemo<AuthState>(
    () => ({ user, token, login, signup, logout, isAuthenticated: !!token }),
    [user, token]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
