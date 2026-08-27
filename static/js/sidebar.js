/**
 * Sidebar de sélection des capteurs (colonne de gauche, onglet Explorer).
 * Rendue entièrement côté client, à partir du même cache /api/series que
 * les onglets Énergie / Consommations par zone (core/api.js). Avant ce
 * refactor, le regroupement (par appartement ou par pièce) était calculé
 * une fois côté serveur (Jinja, pour la sidebar) ET refait côté JS (pour
 * les autres onglets) -- même donnée, deux implémentations à maintenir.
 * Ici il n'y en a plus qu'une, réutilisée par tout le monde.
 *
 * Ce module ne connaît rien de "quel capteur est actuellement
 * sélectionné" ni du graph associé -- ça reste la responsabilité de
 * l'onglet appelant (explorer-tab.js), passée via des callbacks. Ça
 * garde la sidebar réutilisable si un jour un autre onglet a besoin
 * d'une sélection multi-capteurs.
 */

import { loadAllSeries } from "./core/api.js";
import { resourceLabel } from "./core/format.js";
import { getResourceTypeLabels } from "./core/config.js";

let allSeries = [];
let groupMode = "apartment";
const callbacks = {
  isSelected: () => false,
  onSelectionChange: () => {},
};

function apartmentSortKey(name) {
  const m = name.match(/\d+/);
  return m ? [0, parseInt(m[0], 10)] : [1, name];
}

function compareApartments(a, b) {
  const [orderA, valueA] = apartmentSortKey(a);
  const [orderB, valueB] = apartmentSortKey(b);
  if (orderA !== orderB) return orderA - orderB;
  if (typeof valueA === "number" && typeof valueB === "number") return valueA - valueB;
  return String(valueA).localeCompare(String(valueB));
}

/** 2 niveaux : appartement -> libellé du type de ressource -> capteurs. */
function buildApartmentGroups() {
  const labels = getResourceTypeLabels();
  const byApartment = new Map();
  allSeries.forEach((s) => {
    const apt = s.apartment || "Sans appartement";
    const typeLabel = resourceLabel(s.resource_type, labels);
    if (!byApartment.has(apt)) byApartment.set(apt, new Map());
    const byType = byApartment.get(apt);
    if (!byType.has(typeLabel)) byType.set(typeLabel, []);
    byType.get(typeLabel).push(s);
  });
  return Array.from(byApartment.keys())
    .sort(compareApartments)
    .map((apt) => ({
      label: apt,
      types: Array.from(byApartment.get(apt).keys())
        .sort((a, b) => a.localeCompare(b))
        .map((typeLabel) => ({ label: typeLabel, series: byApartment.get(apt).get(typeLabel) })),
    }));
}

/** 1 niveau : pièce -> capteurs (ancienne vue, gardée en bascule). */
function buildRoomGroups() {
  const byRoom = new Map();
  allSeries.forEach((s) => {
    const room = s.room || "Sans pièce";
    if (!byRoom.has(room)) byRoom.set(room, []);
    byRoom.get(room).push(s);
  });
  return Array.from(byRoom.keys())
    .sort((a, b) => a.localeCompare(b))
    .map((room) => ({ label: room, series: byRoom.get(room) }));
}

function checkboxItem(s) {
  const li = document.createElement("li");
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.className = "series-checkbox";
  input.dataset.seriesId = s.series_id;
  input.dataset.label = s.unit ? `${s.label} (${s.unit})` : s.label;
  input.dataset.miniserver = s.miniserver;
  input.checked = callbacks.isSelected(s.series_id);
  input.addEventListener("change", () => {
    callbacks.onSelectionChange(s.series_id, input.checked, { label: input.dataset.label });
  });

  label.appendChild(input);
  label.appendChild(document.createTextNode(s.label));
  if (s.unit) {
    const unitSpan = document.createElement("span");
    unitSpan.className = "unit";
    unitSpan.textContent = s.unit;
    label.appendChild(unitSpan);
  }
  li.appendChild(label);
  return li;
}

function detailsGroup(label, count, className) {
  const details = document.createElement("details");
  details.className = className;
  details.open = true;
  const summary = document.createElement("summary");
  summary.appendChild(document.createTextNode(`${label} `));
  const countSpan = document.createElement("span");
  countSpan.className = "count";
  countSpan.textContent = `(${count})`;
  summary.appendChild(countSpan);
  details.appendChild(summary);
  return details;
}

function seriesList(seriesArr) {
  const ul = document.createElement("ul");
  seriesArr.forEach((s) => ul.appendChild(checkboxItem(s)));
  return ul;
}

function render() {
  const container = document.getElementById("sidebar-tree");
  const hint = document.getElementById("sidebar-empty-hint");
  if (!container) return;
  container.innerHTML = "";

  if (allSeries.length === 0) {
    if (hint) hint.hidden = false;
    return;
  }
  if (hint) hint.hidden = true;

  if (groupMode === "room") {
    buildRoomGroups().forEach((room) => {
      const details = detailsGroup(room.label, room.series.length, "room-group");
      details.appendChild(seriesList(room.series));
      container.appendChild(details);
    });
  } else {
    buildApartmentGroups().forEach((apt) => {
      const total = apt.types.reduce((sum, t) => sum + t.series.length, 0);
      const outer = detailsGroup(apt.label, total, "room-group");
      apt.types.forEach((t) => {
        const inner = detailsGroup(t.label, t.series.length, "type-group");
        inner.appendChild(seriesList(t.series));
        outer.appendChild(inner);
      });
      container.appendChild(outer);
    });
  }
}

function setupToggle() {
  document.querySelectorAll("#sidebar-group-toggle .nav-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#sidebar-group-toggle .nav-toggle-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      groupMode = btn.dataset.group;
      render();
    });
  });
}

/**
 * `isSelected(seriesId) -> bool` : consulté au (re-)rendu pour cocher les
 * cases déjà sélectionnées -- notamment quand on bascule appartement/pièce,
 * la sélection en cours ne doit pas se perdre (elle se perdait avant, ce
 * changement de vue rechargeant toute la page).
 * `onSelectionChange(seriesId, checked, meta)` : appelé à chaque coche.
 */
export async function initSidebar({ isSelected, onSelectionChange } = {}) {
  if (isSelected) callbacks.isSelected = isSelected;
  if (onSelectionChange) callbacks.onSelectionChange = onSelectionChange;
  setupToggle();
  allSeries = await loadAllSeries();
  render();
}

export function clearAllCheckboxes() {
  document.querySelectorAll(".series-checkbox").forEach((cb) => {
    cb.checked = false;
  });
}
