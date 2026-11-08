import { useLocation, useNavigate } from "react-router-dom";

/** Abas da Central de Movimentações: cada tipo tem semântica própria, mas o ponto de entrada é único. */
export function MovementTabs() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  return (
    <div className="tabs" role="tablist">
      <button role="tab" aria-selected={pathname.startsWith("/consumo")} onClick={() => navigate("/consumo")}>Consumo</button>
      <button role="tab" aria-selected={pathname.startsWith("/perdas")} onClick={() => navigate("/perdas")}>Perdas</button>
      <button role="tab" aria-selected={pathname.startsWith("/devolucoes")} onClick={() => navigate("/devolucoes")}>Devoluções</button>
    </div>
  );
}
