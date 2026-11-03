import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { LoginPage } from "./features/auth/LoginPage";
import { ItemPage } from "./features/inventory/ItemPage";
import { SessionPage } from "./features/inventory/SessionPage";
import { SessionsPage } from "./features/inventory/SessionsPage";
import { AuthProvider, useAuth } from "./lib/auth";
import "./styles.css";

function App() {
  const { user, loading } = useAuth();
  if (loading) return <p className="muted center" role="status">Carregando…</p>;
  // Sem redirecionar a URL: ao expirar o login, a pessoa volta para a mesma tela depois de entrar.
  if (!user) return <LoginPage />;
  return (
    <Routes>
      <Route path="/" element={<SessionsPage />} />
      <Route path="/sessoes/:sessionId" element={<SessionPage />} />
      <Route path="/sessoes/:sessionId/itens/:itemId" element={<ItemPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
}
