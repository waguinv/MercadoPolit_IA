import type { Dispatch, SetStateAction } from "react";

import { LOSS_REASON, type LossReason } from "../../lib/losses";

export interface LossFields {
  reason: LossReason;
  quantity: string;
  lot: string;
  expiry: string;
  note: string;
}

/** Campos comuns ao registro e à correção de uma perda. */
export function LossFieldsForm({ fields, set, unitCode }: {
  fields: LossFields; set: Dispatch<SetStateAction<LossFields>>; unitCode?: string;
}) {
  const change = (key: keyof LossFields) => (e: { target: { value: string } }) =>
    set((f) => ({ ...f, [key]: e.target.value }));
  return (
    <>
      <label>
        Motivo
        <select value={fields.reason} onChange={change("reason")}>
          {Object.entries(LOSS_REASON).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>
        Quantidade{unitCode ? ` (${unitCode})` : ""}
        <input inputMode="decimal" required value={fields.quantity} onChange={change("quantity")} />
      </label>
      <div className="row">
        <label className="grow">
          Lote (opcional)
          <input maxLength={64} value={fields.lot} onChange={change("lot")} />
        </label>
        <label className="grow">
          Validade (opcional)
          <input type="date" value={fields.expiry} onChange={change("expiry")} />
        </label>
      </div>
      <label>
        {fields.reason === "other" ? "O que aconteceu" : "Observação (opcional)"}
        <input maxLength={500} required={fields.reason === "other"} value={fields.note} onChange={change("note")} />
      </label>
    </>
  );
}

export const lossPayload = (f: LossFields, productId: string, quantity: string) => ({
  product_id: productId, quantity, reason: f.reason, lot: f.lot.trim() || null,
  expiry_date: f.expiry || null, note: f.note.trim() || null,
});
