/**
 * Tuiles KPI et tableaux du décompte (par zone, immeuble, contrôle).
 * Ne fait aucun appel réseau : reçoit le payload de /api/decompte déjà
 * chargé et le rend.
 */

import { kpiTile, noteEl } from "../core/charts.js";
import { fmtKwh, fmtCHF, fmtPct, fmtPeriodBounds } from "./format.js";

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function row(cells, { head = false, className = "" } = {}) {
  const tr = el("tr", className);
  for (const c of cells) {
    const td = el(head ? "th" : "td", c.className);
    if (c.html !== undefined) td.innerHTML = c.html;
    else td.textContent = c.text;
    if (c.colSpan) td.colSpan = c.colSpan;
    if (c.title) td.title = c.title;
    tr.appendChild(td);
  }
  return tr;
}

function statusCell(entry) {
  if (entry.facturable) return { text: "facturable", className: "status status-ok" };
  if (entry.en_cours) return { text: "mois en cours", className: "status status-pending" };
  return { text: "données", className: "status status-bad", title: entry.alertes.join("\n") };
}

/** Somme d'une valeur sur toutes les zones. Retourne null dès qu'une zone
 * manque : un total partiel affiché comme un total complet ferait
 * sous-estimer la facture de l'immeuble. */
function sumZones(payload, periodKey, pick) {
  let sum = 0;
  for (const z of payload.zones) {
    const e = z.periodes[periodKey];
    const v = e ? pick(e) : null;
    if (v === null || v === undefined) return null;
    sum += v;
  }
  return sum;
}

export function renderKpis(container, notesContainer, payload, periodKey) {
  container.innerHTML = "";
  notesContainer.innerHTML = "";

  const reseau = sumZones(payload, periodKey, (e) => e.reseau.kwh);
  const solaire = sumZones(payload, periodKey, (e) => e.solaire.kwh);
  const ttc = sumZones(payload, periodKey, (e) => e.montants.ttc);
  const total = reseau === null || solaire === null ? null : reseau + solaire;
  const autoprod = total ? (solaire / total) * 100 : null;

  container.appendChild(kpiTile("Consommation totale", fmtKwh(total), "kWh"));
  container.appendChild(kpiTile("Acheté au réseau", fmtKwh(reseau), "kWh", "kpi-grid"));
  container.appendChild(kpiTile("Solaire autoconsommé", fmtKwh(solaire), "kWh", "kpi-solar"));
  container.appendChild(kpiTile("Taux d'autoproduction", autoprod === null ? "—" : fmtPct(autoprod, 0, false), "", "kpi-auto"));
  container.appendChild(kpiTile("Montant TTC", ttc === null ? "—" : fmtCHF(ttc), ""));

  const problemes = [];
  for (const z of payload.zones) {
    const e = z.periodes[periodKey];
    if (e && !e.facturable) problemes.push(`${z.label} : ${e.alertes[0] || "données incomplètes"}`);
  }
  if (problemes.length) {
    notesContainer.appendChild(el("p", "alerte-title", `${problemes.length} zone(s) non facturable(s) sur ce mois :`));
    const ul = el("ul", "alerte-list");
    for (const p of problemes) ul.appendChild(el("li", null, p));
    notesContainer.appendChild(ul);
  } else {
    notesContainer.appendChild(noteEl(
      "Toutes les zones sont facturables sur ce mois. Le taux d'autoproduction est la part " +
      "de la consommation couverte par le solaire de l'immeuble."
    ));
  }
}

export function renderZoneTable(table, payload, periodKey) {
  table.innerHTML = "";
  const thead = el("thead");
  thead.appendChild(row([
    { text: "Zone" },
    { text: "Réseau (kWh)", className: "num" },
    { text: "Solaire (kWh)", className: "num" },
    { text: "Consommation (kWh)", className: "num" },
    { text: "Autoproduction", className: "num" },
    { text: "HT", className: "num" },
    { text: "TVA", className: "num" },
    { text: "TTC", className: "num" },
    { text: "État" },
  ], { head: true }));
  table.appendChild(thead);

  const tbody = el("tbody");
  const totals = { reseau: 0, solaire: 0, total: 0, ht: 0, tva: 0, ttc: 0 };
  // Fiabilité suivie COLONNE PAR COLONNE : sans tarif enregistré, les
  // montants sont indisponibles alors que les kWh, eux, sont parfaitement
  // calculables -- un seul drapeau global effacerait aussi les totaux kWh.
  const ok = { reseau: true, solaire: true, total: true, ht: true, tva: true, ttc: true };

  for (const z of payload.zones) {
    const e = z.periodes[periodKey];
    if (!e) continue;
    const m = e.montants;

    tbody.appendChild(row([
      { text: z.label },
      { text: fmtKwh(e.reseau.kwh, 1), className: "num" },
      { text: fmtKwh(e.solaire.kwh, 1), className: "num" },
      { text: fmtKwh(e.total, 1), className: "num strong" },
      { text: fmtPct(e.taux_autoproduction, 0, false), className: "num" },
      { text: fmtCHF(m.ht), className: "num" },
      { text: fmtCHF(m.tva), className: "num" },
      { text: fmtCHF(m.ttc), className: "num strong" },
      statusCell(e),
    ], { className: e.facturable ? "" : "row-warn" }));

    for (const [k, v] of [["reseau", e.reseau.kwh], ["solaire", e.solaire.kwh], ["total", e.total],
                          ["ht", m.ht], ["tva", m.tva], ["ttc", m.ttc]]) {
      if (v === null) ok[k] = false; else totals[k] += v;
    }
  }
  table.appendChild(tbody);

  const t = (k, f) => (ok[k] ? f(totals[k]) : "—");
  const kwh1 = (v) => fmtKwh(v, 1);
  const autoprodTotal = ok.total && totals.total ? (totals.solaire / totals.total) * 100 : null;

  const tfoot = el("tfoot");
  tfoot.appendChild(row([
    { text: "Total immeuble" },
    { text: t("reseau", kwh1), className: "num" },
    { text: t("solaire", kwh1), className: "num" },
    { text: t("total", kwh1), className: "num strong" },
    { text: fmtPct(autoprodTotal, 0, false), className: "num" },
    { text: t("ht", fmtCHF), className: "num" },
    { text: t("tva", fmtCHF), className: "num" },
    { text: t("ttc", fmtCHF), className: "num strong" },
    { text: "" },
  ], { head: true }));
  table.appendChild(tfoot);
}

export function renderBatimentTable(table, payload, periodes) {
  table.innerHTML = "";
  const thead = el("thead");
  thead.appendChild(row([
    { text: "Mois" },
    { text: "Production PV (kWh)", className: "num" },
    { text: "Autoconsommé (kWh)", className: "num" },
    { text: "Injecté au réseau (kWh)", className: "num" },
    { text: "Acheté au réseau (kWh)", className: "num" },
    { text: "Consommation totale (kWh)", className: "num" },
    { text: "Autoproduction", className: "num" },
    { text: "Autoconsommation", className: "num" },
  ], { head: true }));
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const p of periodes) {
    const b = payload.batiment.periodes[p.key];
    tbody.appendChild(row([
      { text: p.label + (b.en_cours ? " (en cours)" : "") },
      { text: fmtKwh(b.production ? b.production.kwh : null), className: "num" },
      { text: fmtKwh(b.autoconsommation), className: "num" },
      { text: fmtKwh(b.injection), className: "num" },
      { text: fmtKwh(b.achat_reseau), className: "num" },
      { text: fmtKwh(b.consommation_totale), className: "num strong" },
      { text: fmtPct(b.taux_autoproduction, 0, false), className: "num strong" },
      { text: fmtPct(b.taux_autoconsommation, 0, false), className: "num muted-cell" },
    ], { className: b.en_cours ? "row-warn" : "" }));
  }
  table.appendChild(tbody);
}

/** Compteur de contrôle : périmètre DIFFÉRENT des compteurs de facturation,
 * gardé en information. Voir le docstring de billing.py. */
export function renderControleTable(table, payload, periodes) {
  table.innerHTML = "";
  const thead = el("thead");
  const cells = [{ text: "Mois" }];
  for (const z of payload.zones) cells.push({ text: z.label, className: "num" });
  thead.appendChild(row(cells, { head: true }));
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const p of periodes) {
    const line = [{ text: p.label }];
    for (const z of payload.zones) {
      const e = z.periodes[p.key];
      line.push({ text: fmtKwh(e && e.controle ? e.controle.kwh : null, 1), className: "num" });
    }
    tbody.appendChild(row(line));
  }
  table.appendChild(tbody);
}

export function renderSourcesTable(table, payload) {
  table.innerHTML = "";
  const thead = el("thead");
  thead.appendChild(row([
    { text: "Zone" },
    { text: "Réseau — facturé" },
    { text: "Solaire — facturé" },
    { text: "Contrôle — non facturé" },
  ], { head: true }));
  table.appendChild(thead);

  const tbody = el("tbody");
  const cell = (src, ambigus) => {
    if (!src) return { text: "aucune série trouvée", className: "ecart-bad" };
    const extra = ambigus && ambigus.length ? ` (autres candidats : ${ambigus.join(", ")})` : "";
    return { text: src.label + extra, title: src.series_id };
  };
  for (const z of payload.zones) {
    tbody.appendChild(row([
      { text: z.label },
      cell(z.sources.reseau, z.ambigus.reseau),
      cell(z.sources.solaire, z.ambigus.solaire),
      cell(z.sources.controle, z.ambigus.controle),
    ]));
  }

  // Les compteurs d'immeuble ne correspondent pas aux mêmes colonnes que
  // ceux d'une zone : on préfixe chaque cellule de son rôle réel plutôt que
  // de les laisser sous des en-têtes qui ne les décrivent pas.
  const b = payload.batiment.sources;
  const bcell = (role, src) => {
    const c = cell(src);
    c.text = `${role} : ${c.text}`;
    return c;
  };
  tbody.appendChild(row([
    { text: "Immeuble" },
    bcell("production PV", b.production),
    bcell("réseau (autre périmètre)", b.reseau_import),
    bcell("réseau (autre périmètre)", b.reseau_export),
  ], { className: "row-muted" }));
  table.appendChild(tbody);
}

export function renderPeriodBounds(container, period) {
  container.textContent = period ? fmtPeriodBounds(period) : "—";
}
