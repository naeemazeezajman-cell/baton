/* Run: node --test src/selectors.test.mjs */
import assert from "node:assert/strict";
import { test } from "node:test";

import { filterGroups, groupClientActivities, rowNeedsAttention, slimClientActivities } from "./selectors.js";

const NOW = 1_800_000_000_000;
const DAY = 86_400_000;

test("dashboard: completed onboardings hidden, in-progress and not-started shown, capped", () => {
  const acts = [
    { client: "A", onboardingComplete: true },
    { client: "B", onboardingComplete: false, status: "in_progress" },
    { client: "C", onboardingComplete: false }, // pending EL — not yet started
  ];
  const s = slimClientActivities(acts, 8);
  assert.equal(s.visible.length, 2);
  assert.ok(s.visible.every((a) => !a.onboardingComplete));
  assert.equal(s.more, 0);
  assert.equal(s.allComplete, false);

  const capped = slimClientActivities(Array.from({ length: 11 }, (_, i) => ({ client: String(i) })), 8);
  assert.equal(capped.visible.length, 8);
  assert.equal(capped.more, 3);

  const done = slimClientActivities([{ onboardingComplete: true }, { onboardingComplete: true }]);
  assert.equal(done.visible.length, 0);
  assert.equal(done.allComplete, true); // → single "All onboardings complete" line

  assert.equal(slimClientActivities([]).allComplete, false); // nothing staffed ≠ all complete
});

const OB = (id, over = {}) => ({ id, clientId: "c1", clientName: "Gulf Horizon", clientRef: "CL-001",
  service: "VAT Filing", staffId: "priya", status: "in_progress", dutyId: null, ...over });
const DUTY = (id, over = {}) => ({ id, clientId: "c1", client: "Gulf Horizon", service: "VAT Filing",
  staffId: "priya", kind: "vat", closed: false, nextDue: NOW + 5 * DAY, ...over });

test("tab: grouping by client, duty attached via dutyId, duty-only rows included once", () => {
  const groups = groupClientActivities({
    onboardings: [OB("o1", { dutyId: "d1", status: "complete" }),
                  OB("o2", { service: "Corporate Tax Filing" }),
                  OB("o3", { clientId: "c2", clientName: "Ivory Gate", clientRef: "CL-002" })],
    duties: [DUTY("d1"), DUTY("d2", { clientId: "c3", client: "Al Dana", service: "Bookkeeping (Monthly)", kind: "report" })],
    meId: "priya", role: "Staff",
  });
  assert.deepEqual(groups.map((g) => g.clientName), ["Al Dana", "Gulf Horizon", "Ivory Gate"]);
  const gulf = groups.find((g) => g.clientName === "Gulf Horizon");
  assert.equal(gulf.rows.length, 2); // d1 attaches to o1's row, no duplicate duty-only row
  const vatRow = gulf.rows.find((r) => r.service === "VAT Filing");
  assert.equal(vatRow.duty.id, "d1");
  assert.equal(vatRow.onboarding.status, "complete");
  const alDana = groups.find((g) => g.clientName === "Al Dana");
  assert.equal(alDana.rows[0].key, "duty:d2"); // pre-Baton duty with no onboarding
  assert.equal(alDana.rows[0].onboarding, null);
});

test("tab: role scoping — staff own only; managers firm-wide with staff filter", () => {
  const data = {
    onboardings: [OB("o1"), OB("o2", { staffId: "omar", clientId: "c2", clientName: "Ivory Gate" })],
    duties: [],
  };
  const staffView = groupClientActivities({ ...data, meId: "priya", role: "Staff" });
  assert.equal(staffView.length, 1);
  assert.equal(staffView[0].rows[0].staffId, "priya");

  const mgrView = groupClientActivities({ ...data, meId: "imran", role: "Manager" });
  assert.equal(mgrView.flatMap((g) => g.rows).length, 2);

  const filtered = groupClientActivities({ ...data, meId: "imran", role: "Manager", staffFilter: "omar" });
  assert.equal(filtered.flatMap((g) => g.rows).length, 1);
  assert.equal(filtered[0].rows[0].staffId, "omar");
});

test("tab: needs-attention filter and search; links carry resolvable ids", () => {
  const groups = groupClientActivities({
    onboardings: [OB("o1", { status: "complete", dutyId: "d1" }), OB("o2", { status: "in_progress", service: "CT" })],
    duties: [DUTY("d1", { nextDue: NOW - DAY })],  // overdue duty
    meId: "priya", role: "Staff",
  });
  const rows = groups.flatMap((g) => g.rows);
  assert.equal(rowNeedsAttention(rows.find((r) => r.service === "CT"), NOW), true);       // in progress
  assert.equal(rowNeedsAttention(rows.find((r) => r.service === "VAT Filing"), NOW), true); // overdue duty
  assert.equal(rowNeedsAttention({ onboarding: { status: "complete" }, duty: { closed: false, nextDue: NOW + DAY } }, NOW), false);

  const needs = filterGroups(groups, { needsOnly: true, nowMs: NOW });
  assert.equal(needs.flatMap((g) => g.rows).length, 2);

  const searched = filterGroups(groups, { query: "cl-001" });
  assert.ok(searched.length === 1 && searched[0].clientRef === "CL-001");
  assert.equal(filterGroups(groups, { query: "nothing-matches" }).length, 0);

  // links resolve: every row exposes the ids the UI navigates with
  for (const r of rows) {
    assert.ok(r.key);
    if (r.onboarding) assert.ok(r.onboarding.id);
    if (r.duty) assert.ok(r.duty.id && typeof r.duty.kind === "string");
  }
});
