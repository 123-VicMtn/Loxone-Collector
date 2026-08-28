/**
 * Point d'entrée de la page /decompte (templates/decompte.html).
 *
 * Un seul appel à /api/decompte ramène tous les mois disponibles : changer
 * de mois dans le sélecteur ne re-interroge pas le serveur, ça ne fait que
 * re-rendre les tuiles, le tableau et le graph par zone. Les graphs
 * d'évolution / solaire / taux couvrent la plage choisie dans « Historique
 * affiché » et ne dépendent donc pas du mois facturé.
 */

import { fetchDecompte, fetchTarifs } from "./api.js";
import {
  renderKpis, renderZoneTable, renderBatimentTable, renderControleTable,
  renderSourcesTable, renderPeriodBounds,
} from "./table.js";
import {
  renderEvolutionChart, renderZonesChart, renderSolaireChart, renderTauxChart,
} from "./charts.js";
import { initTarifs } from "./tarifs.js";
import { initHealthFooter } from "../core/health.js";

const els = {};
let payload = null;

function selectedPeriod() {
  return payload.periodes.find((p) => p.key === els.periodeSelect.value) || null;
}

/** Mois affichés dans les graphs et les tableaux d'immeuble : à partir de
 * celui choisi dans « Historique affiché ». Indépendant du mois facturé
 * plus haut, qui ne concerne qu'une seule période. */
function visiblePeriods() {
  const i = payload.periodes.findIndex((p) => p.key === els.historiqueSelect.value);
  return i < 0 ? payload.periodes : payload.periodes.slice(i);
}

/** Un mois a des données dès qu'au moins une zone a une consommation
 * calculable. Sert à ne pas ouvrir la page sur une série de barres vides :
 * les compteurs du Moniteur de flux d'énergie n'existent que depuis
 * octobre 2025. */
function hasZoneData(periodKey) {
  return payload.zones.some((z) => {
    const e = z.periodes[periodKey];
    return e && e.total !== null;
  });
}

/** Ce qui dépend du mois choisi. */
function renderPeriod() {
  const period = selectedPeriod();
  if (!period) return;
  renderPeriodBounds(els.periodeBornes, period);
  renderKpis(els.kpis, els.periodeAlertes, payload, period.key);
  renderZoneTable(els.table, payload, period.key);
  renderZonesChart(payload, period.key);
  els.tableHint.textContent = period.label;
  els.zonesHint.textContent = period.label;
}

/** Ce qui couvre la plage de mois affichée. */
function renderRange() {
  const periodes = visiblePeriods();
  renderEvolutionChart(payload, periodes);
  renderSolaireChart(payload, periodes);
  renderTauxChart(payload, periodes);
  renderBatimentTable(els.batimentTable, payload, periodes);
  renderControleTable(els.controleTable, payload, periodes);
}

function renderAll() {
  renderRange();
  renderSourcesTable(els.sourcesTable, payload);
  renderPeriod();
  renderGlobalBanner();
}

/** Bandeau d'en-tête : combien de mois terminés sont facturables. Un mois
 * ne l'est pas quand il manque une donnée (compteur pas encore posé, trou
 * de collecte) -- ce n'est pas un problème d'installation. */
function renderGlobalBanner() {
  const termines = payload.periodes.filter((p) => !payload.batiment.periodes[p.key].en_cours);
  const incomplets = termines.filter((p) =>
    payload.zones.some((z) => z.periodes[p.key] && !z.periodes[p.key].facturable));

  if (!incomplets.length) {
    els.alerte.hidden = true;
    return;
  }
  els.alerte.hidden = false;
  els.alerte.innerHTML =
    `<strong>${termines.length - incomplets.length} mois facturables sur ${termines.length} mois terminés.</strong> ` +
    `Données insuffisantes sur : ${incomplets.map((p) => p.label).join(", ")}. ` +
    "Le détail par zone est dans la colonne « État » du tableau — il s'agit de mois " +
    "antérieurs à la pose des compteurs, ou de trous de collecte, pas d'une anomalie " +
    "de comptage.";
}

async function reload() {
  payload = await fetchDecompte();
  renderAll();
}

document.addEventListener("DOMContentLoaded", async () => {
  Object.assign(els, {
    loading: document.getElementById("decompte-loading"),
    body: document.getElementById("decompte-body"),
    alerte: document.getElementById("decompte-alerte"),
    periodeSelect: document.getElementById("periode-select"),
    historiqueSelect: document.getElementById("historique-select"),
    periodeBornes: document.getElementById("periode-bornes"),
    kpis: document.getElementById("periode-kpis"),
    periodeAlertes: document.getElementById("periode-alertes"),
    table: document.getElementById("decompte-table"),
    tableHint: document.getElementById("decompte-table-hint"),
    zonesHint: document.getElementById("chart-zones-hint"),
    batimentTable: document.getElementById("batiment-table"),
    controleTable: document.getElementById("controle-table"),
    sourcesTable: document.getElementById("sources-table"),
    tarifsTable: document.getElementById("tarifs-table"),
    tarifForm: document.getElementById("tarif-form"),
    tarifMessage: document.getElementById("tarif-message"),
  });

  initHealthFooter();

  try {
    const [data, tarifs] = await Promise.all([fetchDecompte(), fetchTarifs()]);
    payload = data;

    if (!payload.periodes.length || !payload.zones.length) {
      els.loading.textContent =
        "Aucune donnée exploitable pour un décompte : il faut au moins une zone " +
        "avec des compteurs cumulatifs (state « total ») en base.";
      return;
    }

    // Mois proposé par défaut : le dernier mois TERMINÉ qui a des données,
    // c'est-à-dire celui qu'on cherche à facturer -- ni le mois en cours
    // (incomplet), ni un mois antérieur aux compteurs. Les replis successifs
    // couvrent une installation trop récente pour avoir un mois terminé.
    const dernier = (liste) => liste[liste.length - 1];
    const termines = payload.periodes.filter((p) => !payload.batiment.periodes[p.key].en_cours);
    const defaut = (
      dernier(termines.filter((p) => hasZoneData(p.key))) ||
      dernier(payload.periodes.filter((p) => hasZoneData(p.key))) ||
      dernier(termines) ||
      dernier(payload.periodes)
    ).key;

    for (const p of payload.periodes) {
      const suffix = payload.batiment.periodes[p.key].en_cours ? " (en cours)" : "";
      for (const [select, texte] of [
        [els.periodeSelect, p.label + suffix],
        [els.historiqueSelect, `depuis ${p.label}`],
      ]) {
        const opt = document.createElement("option");
        opt.value = p.key;
        opt.textContent = texte;
        select.appendChild(opt);
      }
    }
    els.periodeSelect.value = defaut;
    els.periodeSelect.addEventListener("change", renderPeriod);

    const premierAvecDonnees = payload.periodes.find((p) => hasZoneData(p.key));
    els.historiqueSelect.value = (premierAvecDonnees || payload.periodes[0]).key;
    els.historiqueSelect.addEventListener("change", renderRange);

    initTarifs({
      table: els.tarifsTable,
      form: els.tarifForm,
      message: els.tarifMessage,
      tarifs,
      onChange: reload,
    });

    renderAll();
    els.loading.hidden = true;
    els.body.hidden = false;
  } catch (err) {
    console.error(err);
    els.loading.textContent = `Impossible de charger le décompte : ${err.message}`;
  }
});
