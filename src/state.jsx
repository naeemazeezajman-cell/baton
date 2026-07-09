/* Server data layer — replaces the prototype App's useState blocks. Data is fetched per
   screen with a tiny cache; every prototype `actions.*` mutation becomes an API call
   followed by a refetch of the affected entities. No optimistic writes in v1.
   Adapters map API shapes (snake_case, ISO timestamps, UUIDs) onto the exact object
   shapes the prototype components consume (camelCase, epoch-ms, ref strings). */

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, rawFromUrl, uploadFile } from "./api.js";

const DataCtx = createContext(null);
export const useData = () => useContext(DataCtx);

const ms = (iso) => (iso ? new Date(iso).getTime() : null);
const fileUrl = (id) => (id ? `api://file/${id}` : null);

/* ---------- adapters: API → prototype shapes ---------- */

const mapDraft = (d) =>
  d && d.lines
    ? { lines: d.lines, paymentTerms: d.payment_terms ?? d.paymentTerms ?? "", validityDays: d.validity_days ?? d.validityDays ?? 30, scope: d.scope ?? "" }
    : { lines: [], paymentTerms: "", validityDays: 30, scope: "" };

const unmapDraft = (d) => ({
  lines: d.lines.map((l) => ({ service: l.service, fee: l.fee, basis: l.basis })),
  payment_terms: d.paymentTerms || "",
  validity_days: Number(d.validityDays) || 30,
  scope: d.scope || "",
});

const mapSig = (s) => (s ? { by: s.by, at: ms(s.at) } : null);
const mapNote = (n) => (n ? { by: n.by, at: ms(n.at), text: n.text, note: n.note, stage: n.stage } : null);

const CHAT_RE = /^Chat: "([\s\S]*)"$/;

function mapProposal(a, docs = []) {
  const events = (a.events || []).map((e, i) => ({
    id: `${a.id}-e${i}`,
    at: ms(e.at),
    by: e.by || "system",
    kind: e.kind,
    text: e.text,
  }));
  return {
    id: a.ref,
    uuid: a.id,
    createdAt: ms(a.created_at),
    prospect: a.prospect || {},
    notes: a.prospect?.notes || "",
    services: a.services || [],
    requestedBy: a.requested_by,
    assignedTo: a.assigned_to,
    holder: a.holder,
    signatoryId: a.signatory_id,
    clientId: a.client_id,
    status: a.status,
    proposalSentAt: ms(a.proposal_sent_at),
    checklist: (a.checklist || []).map((s) => ({
      id: s.id, kind: s.kind, label: s.label, status: s.status, value: s.value || "",
      fileName: s.file_name || "", fileUrl: fileUrl(s.file_id), fileSize: null, reason: s.reason || "",
    })),
    versions: (a.versions || []).map((v) => ({
      v: v.v, at: ms(v.at), by: v.by, data: mapDraft(v.data), note: v.note, signatures: v.signatures,
    })),
    draft: mapDraft(a.draft),
    signatures: { manager: mapSig(a.signatures?.manager), senior: mapSig(a.signatures?.senior) },
    revisionNote: mapNote(a.revision_note),
    seniorNote: mapNote(a.senior_note),
    lastRejection: mapNote(a.last_rejection),
    el: a.el && Object.keys(a.el).length
      ? {
          note: a.el.note || "", advancePct: a.el.advance_pct || 0, signatoryId: a.el.signatory_id || null,
          signature: mapSig(a.el.signature), sentAt: ms(a.el.sent_at), assignments: a.el.assignments || {},
        }
      : null,
    clientSignedProposal: a.el?.client_signed
      ? { name: a.el.client_signed.name, url: fileUrl(a.el.client_signed.file_id), at: ms(a.el.client_signed.at) }
      : null,
    docs: docs.map((f) => ({ id: f.id, name: f.name, url: fileUrl(f.id), size: f.size, by: f.uploaded_by, at: ms(f.at) })),
    holderLog: (a.holder_log || []).map((h) => ({ userId: h.user_id, start: ms(h.started_at), end: ms(h.ended_at), reason: h.reason })),
    events,
    chat: events
      .filter((e) => e.kind === "chat")
      .map((e) => ({ id: e.id, by: e.by, at: e.at, text: (CHAT_RE.exec(e.text) || [null, e.text])[1] })),
  };
}

const mapUser = (u) => ({
  id: u.id, name: u.name, designation: u.designation, email: u.email, role: u.role,
  signatory: u.signatory, sigSpecimen: u.sig_specimen || null, active: u.active,
});

const mapDuty = (d) => ({
  id: d.id, staffId: d.staff_id, client: d.client_name, service: d.service,
  kind: d.kind, contact: d.contact || { name: "", email: "" }, cadence: d.cadence,
  nextDue: ms(d.next_due), closed: d.closed,
  history: (d.history || []).map((h) => ({
    dueAt: ms(h.due_at), completedAt: ms(h.completed_at), lateMs: h.late_ms, method: h.method,
    note: h.note || "", reason: h.reason || "", emailedTo: h.emailed_to || "", record: h.record,
    evidence: (h.evidence || []).map((f) => ({ name: f.name, url: fileUrl(f.file_id), size: f.size })),
  })),
  events: (d.events || []).map((e, i) => ({ id: `${d.id}-e${i}`, at: ms(e.at), by: e.by || "system", text: e.text })),
});

const mapPayment = (x, clientsById, refByProposalUuid) => ({
  id: x.id, clientId: x.client_id,
  clientName: clientsById[x.client_id]?.name || "",
  pid: refByProposalUuid[x.proposal_id] || "",
  label: x.label, amount: x.amount, dueAt: ms(x.due_at),
  invoiceRaised: x.invoice_raised, received: x.received, done: x.done,
  evidence: (x.receipts || []).filter((r) => r.file_id).map((r) => ({ name: r.file_name, url: fileUrl(r.file_id), amount: r.amount })),
  events: (x.events || []).map((e, i) => ({ id: `${x.id}-e${i}`, at: ms(e.at), by: e.by, text: e.text })),
});

const mapFirm = (t) => ({
  id: t.id, name: t.name, short: t.short, address: t.address || "", trn: t.trn || "",
  phone: t.phone || "", email: t.email, accent: t.accent || "#1E6E56",
  services: t.services || [],
  templates: Object.fromEntries(
    Object.entries(t.templates || {}).map(([k, v]) => [k, v ? { name: v.name, url: fileUrl(v.file_id) } : null])
  ),
});

/* ---------- provider ---------- */

export function DataProvider({ me, firm: firmRaw, onFirmChanged, children }) {
  const [users, setUsers] = useState([]);
  const [proposals, setProposals] = useState([]);
  const [clientsRaw, setClientsRaw] = useState([]);
  const [duties, setDuties] = useState([]);
  const [paymentsRaw, setPaymentsRaw] = useState([]);
  const [notices, setNotices] = useState([]);
  const [sigUses, setSigUses] = useState([]);
  const [toast, setToast] = useState(null);
  const [ready, setReady] = useState(false);
  const detailDocs = useRef({}); // proposal uuid → files
  const detailFull = useRef({}); // proposal uuid → full API proposal (with events)

  const firm = mapFirm(firmRaw);
  const isAcct = me.role === "Accountant";
  const isAdmin = me.role === "Admin";

  const pushToast = (t) => { setToast(t); setTimeout(() => setToast(null), 3000); };

  const refetchAll = useCallback(async () => {
    const [u, p, c, d, n] = await Promise.all([
      api.get("/users"),
      api.get("/proposals"),
      api.get("/clients"),
      api.get("/duties"),
      api.get("/notices"),
    ]);
    setUsers(u.filter((x) => x.active).map(mapUser));
    setProposals(p);
    setClientsRaw(c);
    setDuties(d.map(mapDuty));
    setNotices(n.map((x) => ({ id: x.id, userId: me.id, at: ms(x.at), text: x.text, read: x.read })));
    if (isAcct || isAdmin) setPaymentsRaw(await api.get("/payments").catch(() => []));
    if (isAdmin) setSigUses((await api.get("/signature-uses").catch(() => [])).map(
      (s) => ({ id: s.id, by: s.by, doc: s.document, pid: s.context, at: ms(s.at) })
    ));
    setReady(true);
  }, [me.id]);

  useEffect(() => { refetchAll(); }, [refetchAll]);

  const refetchDetail = useCallback(async (uuid) => {
    const [full, docs] = await Promise.all([
      api.get(`/proposals/${uuid}`),
      api.get(`/files?entity=proposal&entity_id=${uuid}`),
    ]);
    detailFull.current[uuid] = full;
    detailDocs.current[uuid] = docs;
    setProposals((ps) => ps.map((x) => (x.id === uuid ? full : x)));
  }, []);

  /* proposals in prototype shape, detail-enriched where fetched */
  const proposalsMapped = proposals.map((a) =>
    mapProposal(detailFull.current[a.id] || a, detailDocs.current[a.id] || [])
  );
  const byRef = Object.fromEntries(proposalsMapped.map((p) => [p.id, p]));
  const uuidOf = (ref) => byRef[ref]?.uuid;
  const refByUuid = Object.fromEntries(proposalsMapped.map((p) => [p.uuid, p.id]));
  const clientsById = Object.fromEntries(clientsRaw.map((c) => [c.id, c]));

  const clients = clientsRaw.map((c) => {
    const pr = proposalsMapped.find((p) => p.uuid === c.from_proposal);
    return {
      id: c.id, code: c.ref, name: c.name, contact: c.contact,
      pid: pr?.id || null, engagedAt: ms(c.created_at),
      services: pr ? (pr.versions.at(-1)?.data.lines || []).map((l) => l.service) : [],
    };
  });
  const payments = paymentsRaw.map((x) => mapPayment(x, clientsById, refByUuid));

  /* wrap an API mutation: run, toast failures (409 reasons verbatim), refetch */
  const act = (fn, { detail } = {}) => async (...args) => {
    try {
      const out = await fn(...args);
      await refetchAll();
      if (detail) {
        const uuid = typeof detail === "function" ? detail(...args) : uuidOf(args[0]);
        if (uuid) await refetchDetail(uuid);
      }
      return out;
    } catch (e) {
      pushToast(`⚠️ ${e.message}`);
      throw e;
    }
  };
  const onRef = { detail: true };

  const uploadRaw = (entity, entityId) => async (fileDesc) => {
    const raw = rawFromUrl(fileDesc.url);
    if (!raw) throw new Error(`File ${fileDesc.name} is no longer available — re-attach it`);
    return uploadFile(entity, entityId, raw);
  };

  /* ---------- actions: prototype signatures, API implementations ---------- */

  const actions = {
    createRequest: act(async (form) => {
      const created = await api.post("/proposals", {
        prospect: { ...form.prospect, notes: form.notes || null },
        services: form.services,
        assigned_to: form.assignedTo,
        payment_terms_rough: form.paymentTerms || null,
      });
      for (const d of form.docs) await uploadRaw("proposal", created.id)(d);
      pushToast(`Request ${created.ref} created · auto-email sent to the drafter`);
      return created;
    }),

    sendChecklist: act((ref, slots) => api.post(`/proposals/${uuidOf(ref)}/request-items`, { slots }), onRef),

    fulfillSlot: act(async (ref, slotId, patch) => {
      const uuid = uuidOf(ref);
      if (patch.status === "provided" && patch.fileUrl) {
        const f = await uploadRaw("proposal", uuid)({ url: patch.fileUrl, name: patch.fileName });
        return api.post(`/proposals/${uuid}/provide-item`, { slot_id: slotId, file_id: f.id });
      }
      if (patch.status === "provided") return api.post(`/proposals/${uuid}/provide-item`, { slot_id: slotId, value: patch.value });
      if (patch.status === "waiver_requested") return api.post(`/proposals/${uuid}/waive`, { slot_id: slotId, action: "request", reason: patch.reason });
      if (patch.status === "waived") return api.post(`/proposals/${uuid}/waive`, { slot_id: slotId, action: "approve" });
      if (patch.status === "pending") return api.post(`/proposals/${uuid}/waive`, { slot_id: slotId, action: "still-required" });
      if (patch.status === "rejected") return api.post(`/proposals/${uuid}/reject-item`, { slot_id: slotId, reason: patch.reason });
      throw new Error(`Unknown slot update ${patch.status}`);
    }, onRef),

    withdrawSlot: act((ref, slotId, reason) => api.post(`/proposals/${uuidOf(ref)}/withdraw-item`, { slot_id: slotId, reason }), onRef),
    managerReturn: act((ref) => api.post(`/proposals/${uuidOf(ref)}/return-checklist`), onRef),
    staffSendBack: act((ref) => api.post(`/proposals/${uuidOf(ref)}/send-back`), onRef),
    startDrafting: act((ref) => api.post(`/proposals/${uuidOf(ref)}/start-drafting`), onRef),

    generateVersion: act((ref, draft, note) =>
      api.post(`/proposals/${uuidOf(ref)}/generate`, { draft: unmapDraft(draft), note }), onRef),

    submitToManager: act((ref) => api.post(`/proposals/${uuidOf(ref)}/submit`, { version: byRef[ref].versions.length }), onRef),
    sendForRevision: act((ref, comment) => api.post(`/proposals/${uuidOf(ref)}/send-for-revision`, { comment }), onRef),
    sendChat: act((ref, text) => api.post(`/proposals/${uuidOf(ref)}/chat`, { text }), onRef),
    managerSignRoute: act((ref, signatoryId, note) =>
      api.post(`/proposals/${uuidOf(ref)}/sign-route`, { signatory_id: signatoryId, note: note || null }), onRef),
    seniorApprove: act((ref) => api.post(`/proposals/${uuidOf(ref)}/senior-approve`), onRef),
    seniorReject: act((ref, note) => api.post(`/proposals/${uuidOf(ref)}/senior-reject`, { note }), onRef),

    sendClientEmail: act(async (ref, kind, mail) => {
      const path = kind === "el" ? "el-send" : "send-client";
      const out = await api.post(`/proposals/${uuidOf(ref)}/${path}`, { to: mail.to, subject: mail.subject, body: mail.body });
      if (kind === "el") pushToast(`EL sent — Onboarding Part 1 complete 🎉`);
      return out;
    }, onRef),

    markLost: act((ref, note) => api.post(`/proposals/${uuidOf(ref)}/mark-lost`, { note: note || null }), onRef),

    uploadSignedProposal: act(async (ref, file) => {
      const raw = rawFromUrl(file.url);
      const fd = new FormData();
      fd.append("file", raw, file.name);
      const out = await api.postForm(`/proposals/${uuidOf(ref)}/upload-signed`, fd);
      pushToast(`${byRef[ref].prospect.name} confirmed — now client ${out.client.ref}. EL prepared; assign staff per activity.`);
      return out;
    }, onRef),

    assignActivity: act((ref, service, staffId) =>
      api.post(`/proposals/${uuidOf(ref)}/staff-activity`, { service, staff_id: staffId }), onRef),
    setELAdvance: act((ref, pct) => api.post(`/proposals/${uuidOf(ref)}/el-plan`, { advance_pct: pct }), onRef),
    setELNote: act((ref, text) => api.post(`/proposals/${uuidOf(ref)}/el-note`, { note: text || "" }), onRef),
    routeEL: act((ref, signatoryId) => api.post(`/proposals/${uuidOf(ref)}/el-route`, { signatory_id: signatoryId }), onRef),
    elApprove: act((ref) => api.post(`/proposals/${uuidOf(ref)}/el-sign`), onRef),
    elReject: act((ref, note) => api.post(`/proposals/${uuidOf(ref)}/el-reject`, { note }), onRef),
  };

  const markDutyDone = act(async (dutyId, payload) => {
    const fd = new FormData();
    fd.append("method", payload.method || "declared");
    fd.append("note", payload.note || "");
    fd.append("reason", payload.reason || "");
    fd.append("emailed_to", payload.emailedTo || "");
    fd.append("record", payload.record ? JSON.stringify(payload.record) : "");
    for (const f of payload.files || []) {
      const raw = rawFromUrl(f.url);
      if (raw) fd.append("evidence", raw, f.name);
    }
    return api.postForm(`/duties/${dutyId}/complete`, fd);
  });

  const markInvoiceRaised = act((payId) => api.post(`/payments/${payId}/invoice-raised`));
  const recordReceipt = act(async (payId, amount, file) => {
    const fd = new FormData();
    fd.append("amount", String(amount));
    if (file) {
      const raw = rawFromUrl(file.url);
      if (raw) fd.append("evidence", raw, file.name);
    }
    return api.postForm(`/payments/${payId}/record-receipt`, fd);
  });

  const markNoticesRead = async () => {
    const unread = notices.filter((n) => !n.read);
    await Promise.all(unread.map((n) => api.post(`/notices/${n.id}/read`).catch(() => {})));
    setNotices((ns) => ns.map((n) => ({ ...n, read: true })));
  };

  /* Admin screens keep their prototype setter props; the shims diff and call the API. */
  const setUsersShim = act(async (newList) => {
    const added = newList.filter((u) => !users.some((x) => x.id === u.id));
    for (const u of added) {
      await api.post("/users", { name: u.name, designation: u.designation || null, email: u.email, role: u.role, signatory: !!u.signatory });
    }
    if (added.length) pushToast(`Invite sent to ${added.map((u) => u.name).join(", ")}`);
  });

  const setFirmShim = act(async (newFirm) => {
    const templates = {};
    for (const [k, v] of Object.entries(newFirm.templates || {})) {
      if (v && rawFromUrl(v.url)) {
        const f = await uploadFile("tenant", firm.id, rawFromUrl(v.url));
        templates[k] = { name: f.name, file_id: f.id };
      } else if (v && v.url?.startsWith("api://file/")) {
        templates[k] = { name: v.name, file_id: v.url.slice("api://file/".length) };
      } else {
        templates[k] = null;
      }
    }
    await api.patch("/tenants/me", {
      name: newFirm.name, short: newFirm.short, address: newFirm.address, trn: newFirm.trn,
      phone: newFirm.phone, email: newFirm.email, accent: newFirm.accent,
      services: newFirm.services, templates,
    });
    await onFirmChanged?.();
  });

  return (
    <DataCtx.Provider value={{
      ready, me, firm, users, proposals: proposalsMapped, clients, duties, payments,
      notices, sigUses, toast, pushToast, refetchAll, refetchDetail, uuidOf,
      actions, markDutyDone, markInvoiceRaised, recordReceipt, markNoticesRead,
      setUsersShim, setFirmShim,
    }}>
      {children}
    </DataCtx.Provider>
  );
}
