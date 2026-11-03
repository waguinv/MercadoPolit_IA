import { useState } from "react";
import { useParams } from "react-router-dom";

import { Badge, Loaded, Shell, useLoad } from "../../components/ui";
import { cachedGet } from "../../lib/api";
import { canCount, canReview, useUser } from "../../lib/auth";
import { SESSION_STATUS } from "../../lib/format";
import type { SessionDetail } from "../../lib/types";
import { CountTab } from "./CountTab";
import { ReviewTab } from "./ReviewTab";
import { useLocations } from "./SessionsPage";

export function SessionPage() {
  const { sessionId = "" } = useParams();
  const user = useUser();
  const session = useLoad(() => cachedGet<SessionDetail>(`/inventory-sessions/${sessionId}`, user.id), [sessionId, user.id]);
  const locations = useLocations(user.id);
  const both = canCount(user) && canReview(user);
  const [tab, setTab] = useState<"count" | "review">(canCount(user) ? "count" : "review");

  return (
    <Shell title={session.data?.data.name ?? "Sessão"} back="/">
      <Loaded state={session}>
        {({ data, stale }) => {
          const location = locations.data?.find((l) => l.id === data.location_id);
          return (
            <>
              <div className="row between">
                <span className="muted">
                  {location ? `${location.storeName} · ${location.name}` : "Local"}{data.sector ? ` · ${data.sector}` : ""}
                </span>
                <Badge status={data.status} label={SESSION_STATUS[data.status]} />
              </div>
              {stale && <p className="notice warn">Sem conexão: a contagem fica guardada no aparelho e é enviada depois.</p>}
              {both && (
                <div className="tabs" role="tablist">
                  <button role="tab" aria-selected={tab === "count"} onClick={() => setTab("count")}>Contar</button>
                  <button role="tab" aria-selected={tab === "review"} onClick={() => setTab("review")}>Revisar</button>
                </div>
              )}
              {tab === "count"
                ? <CountTab session={data} onChanged={session.reload} />
                : <ReviewTab session={data} onChanged={session.reload} />}
            </>
          );
        }}
      </Loaded>
    </Shell>
  );
}
