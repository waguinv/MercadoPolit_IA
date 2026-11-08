import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { ErroDaTela } from "./components/Suporte";
import { Loading } from "./components/ui";
import { AccountPage } from "./features/account/AccountPage";
import { ConsumptionPage } from "./features/consumption/ConsumptionPage";
import { ConsumptionsPage } from "./features/consumption/ConsumptionsPage";
import { LoginPage } from "./features/auth/LoginPage";
import { ItemPage } from "./features/inventory/ItemPage";
import { SessionPage } from "./features/inventory/SessionPage";
import { SessionsPage } from "./features/inventory/SessionsPage";
import { ReceivingPage } from "./features/receiving/ReceivingPage";
import { ReceivingsPage } from "./features/receiving/ReceivingsPage";
import { LossPage } from "./features/losses/LossPage";
import { LossesPage } from "./features/losses/LossesPage";
import { SettingsPage } from "./features/settings/SettingsPage";
import { AuthProvider, isAdmin, useAuth } from "./lib/auth";
import "./tokens.css";
import "./styles.css";

function App() {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  // Sem redirecionar a URL: ao expirar o login, a pessoa volta para a mesma tela depois de entrar.
  if (!user) return <LoginPage />;
  return (
    <Routes>
      <Route path="/" element={<SessionsPage />} />
      <Route path="/sessoes/:sessionId" element={<SessionPage />} />
      <Route path="/sessoes/:sessionId/itens/:itemId" element={<ItemPage />} />
      <Route path="/recebimentos" element={<ReceivingsPage />} />
      <Route path="/recebimentos/:receivingId" element={<ReceivingPage />} />
      <Route path="/consumo" element={<ConsumptionsPage />} />
      <Route path="/consumo/:consumptionId" element={<ConsumptionPage />} />
      <Route path="/perdas" element={<LossesPage />} />
      <Route path="/perdas/:lossId" element={<LossPage />} />
      <Route path="/conta" element={<AccountPage />} />
      {isAdmin(user) && <Route path="/configuracao" element={<SettingsPage />} />}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

// Erro pego pela tela de erro: o registro fica com o ErroDaTela (código, tela e tipo), sem a mensagem bruta.
createRoot(document.getElementById("root")!, { onCaughtError: () => undefined }).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <ErroDaTela>
          <App />
        </ErroDaTela>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
}
