/* Pure selectors for the dashboard to-do list and the My clients directory.
   No React, no JSX — unit-tested with the node built-in test runner
   (`node --test src/selectors.test.mjs`). */

/* Dashboard "Your client activities": ONLY items needing attention — in-progress
   onboardings (with the viewer, or awaiting the manager) and not-yet-started activities.
   Completed onboardings disappear entirely. Capped, with a "+N more" count. */
export function slimClientActivities(activities, cap = 8) {
  const attention = activities.filter((a) => !a.onboardingComplete);
  return {
    visible: attention.slice(0, cap),
    more: Math.max(0, attention.length - cap),
    allComplete: attention.length === 0 && activities.length > 0,
  };
}

/* One row per client-activity the person is staffed on, grouped by client.
   Staff see their own; managers/admins see firm-wide with an optional staff filter. */
export function groupClientActivities({ onboardings = [], duties = [], meId, role, staffFilter = null }) {
  const firmWide = role === "Manager" || role === "Admin";
  const visible = (staffId) => (firmWide ? !staffFilter || staffId === staffFilter : staffId === meId);

  const rows = [];
  for (const ob of onboardings) {
    if (!visible(ob.staffId)) continue;
    rows.push({
      key: `ob:${ob.id}`,
      clientKey: String(ob.clientId || ob.clientName || "—"),
      clientName: ob.clientName || "—",
      clientRef: ob.clientRef || null,
      service: ob.service,
      staffId: ob.staffId,
      onboarding: ob,
      duty: (ob.dutyId && duties.find((d) => d.id === ob.dutyId)) || null,
    });
  }
  for (const d of duties) {
    if (d.closed || !visible(d.staffId)) continue;
    if (rows.some((r) => r.duty && r.duty.id === d.id)) continue;
    rows.push({
      key: `duty:${d.id}`,
      clientKey: String(d.clientId || d.client || "—"),
      clientName: d.client || "—",
      clientRef: null,
      service: d.service,
      staffId: d.staffId,
      onboarding: null,
      duty: d,
    });
  }

  const groups = new Map();
  for (const r of rows) {
    if (!groups.has(r.clientKey)) {
      groups.set(r.clientKey, { clientKey: r.clientKey, clientName: r.clientName,
                                clientRef: r.clientRef, rows: [] });
    }
    const g = groups.get(r.clientKey);
    g.clientRef = g.clientRef || r.clientRef;
    g.rows.push(r);
  }
  const out = [...groups.values()];
  out.sort((a, b) => a.clientName.localeCompare(b.clientName));
  for (const g of out) g.rows.sort((a, b) => a.service.localeCompare(b.service));
  return out;
}

export function rowNeedsAttention(row, nowMs) {
  if (row.onboarding && row.onboarding.status === "in_progress") return true;
  if (row.duty && !row.duty.closed && row.duty.nextDue < nowMs) return true;
  return false;
}

export function filterGroups(groups, { query = "", needsOnly = false, nowMs = 0 } = {}) {
  const q = query.trim().toLowerCase();
  return groups
    .map((g) => ({
      ...g,
      rows: g.rows.filter((r) =>
        (!needsOnly || rowNeedsAttention(r, nowMs))
        && (!q || `${g.clientName} ${g.clientRef || ""} ${r.service}`.toLowerCase().includes(q))),
    }))
    .filter((g) => g.rows.length > 0);
}
