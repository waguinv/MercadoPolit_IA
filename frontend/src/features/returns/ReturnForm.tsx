import type { Dispatch, SetStateAction } from "react";

import { useLoad } from "../../components/ui";
import { api } from "../../lib/api";
import {
  receivingLabel, RETURN_MODALITY, RETURN_REASON, type ReturnModality, type ReturnReason, type SupplierReturn,
} from "../../lib/returns";
import type { Receiving } from "../../lib/types";

export interface ReturnFields {
  reason: ReturnReason;
  modality: ReturnModality;
  receivingId: string;
  supplier: string;
  originDocument: string;
  quantity: string;
  lot: string;
  expiry: string;
  note: string;
  pickupDate: string;
  replacementDate: string;
}

export const emptyReturnFields: ReturnFields = {
  reason: "damaged", modality: "exchange", receivingId: "", supplier: "", originDocument: "", quantity: "",
  lot: "", expiry: "", note: "", pickupDate: "", replacementDate: "",
};

export const fieldsOf = (item: SupplierReturn, quantity: string): ReturnFields => ({
  reason: item.reason, modality: item.modality, receivingId: item.receiving_id ?? "", supplier: item.supplier_name,
  originDocument: item.origin_document ?? "", quantity, lot: item.lot ?? "", expiry: item.expiry_date ?? "",
  note: item.note ?? "", pickupDate: item.expected_pickup_date ?? "", replacementDate: item.expected_replacement_date ?? "",
});

/** Campos comuns ao registro e à correção de uma devolução. */
export function ReturnFieldsForm({ fields, set, storeId, unitCode }: {
  fields: ReturnFields; set: Dispatch<SetStateAction<ReturnFields>>; storeId: string; unitCode?: string;
}) {
  const receivings = useLoad(() => api<Receiving[]>("/receivings?status=finalized"), []);
  const options = (receivings.data ?? []).filter((r) => r.store_id === storeId);
  const change = (key: keyof ReturnFields) => (e: { target: { value: string } }) =>
    set((f) => ({ ...f, [key]: e.target.value }));
  return (
    <>
      <label>
        Recebimento de origem (opcional)
        <select value={fields.receivingId} onChange={change("receivingId")}>
          <option value="">Não passou pelo recebimento do sistema</option>
          {options.map((r) => <option key={r.id} value={r.id}>{receivingLabel(r)}</option>)}
        </select>
      </label>
      {!fields.receivingId && (
        <>
          <label>
            Fornecedor
            <input required maxLength={200} value={fields.supplier} onChange={change("supplier")} />
          </label>
          <label>
            Número da NF de origem (opcional)
            <input maxLength={20} value={fields.originDocument} onChange={change("originDocument")} />
          </label>
        </>
      )}
      <label>
        Modalidade
        <select value={fields.modality} onChange={change("modality")}>
          {Object.entries(RETURN_MODALITY).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>
        Motivo
        <select value={fields.reason} onChange={change("reason")}>
          {Object.entries(RETURN_REASON).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
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
      <div className="row">
        <label className="grow">
          Coleta prevista (opcional)
          <input type="date" value={fields.pickupDate} onChange={change("pickupDate")} />
        </label>
        {fields.modality === "exchange" && (
          <label className="grow">
            Reposição prevista (opcional)
            <input type="date" value={fields.replacementDate} onChange={change("replacementDate")} />
          </label>
        )}
      </div>
      <label>
        {fields.reason === "other" ? "O que aconteceu" : "Observação (opcional)"}
        <input maxLength={500} required={fields.reason === "other"} value={fields.note} onChange={change("note")} />
      </label>
    </>
  );
}

export const returnPayload = (f: ReturnFields, productId: string, quantity: string) => ({
  product_id: productId, quantity, reason: f.reason, modality: f.modality,
  receiving_id: f.receivingId || null,
  supplier_name: f.receivingId ? null : f.supplier.trim() || null,
  origin_document: f.receivingId ? null : f.originDocument.trim() || null,
  lot: f.lot.trim() || null, expiry_date: f.expiry || null, note: f.note.trim() || null,
  expected_pickup_date: f.pickupDate || null,
  expected_replacement_date: f.modality === "exchange" ? f.replacementDate || null : null,
});
