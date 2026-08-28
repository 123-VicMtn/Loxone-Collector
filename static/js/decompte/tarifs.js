/**
 * Panneau des tarifs : liste des tarifs enregistrés + formulaire de saisie.
 * Chaque tarif prend effet à une date et reste appliqué aux mois
 * suivants jusqu'au tarif suivant (voir billing.tarif_for côté serveur).
 */

import { saveTarif, deleteTarif } from "./api.js";
import { fmtCHF, fmtDay } from "./format.js";

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function renderTable(table, tarifs, onDelete) {
  table.innerHTML = "";
  if (!tarifs.length) {
    const tbody = el("tbody");
    const tr = el("tr");
    const td = el("td", "empty-hint",
      "Aucun tarif enregistré : les colonnes HT / TVA / TTC du décompte restent vides " +
      "tant qu'aucun prix n'est saisi ci-dessous.");
    td.colSpan = 6;
    tr.appendChild(td);
    tbody.appendChild(tr);
    table.appendChild(tbody);
    return;
  }

  const thead = el("thead");
  const htr = el("tr");
  for (const h of ["Valable dès le", "Réseau", "Solaire", "TVA", "Note", ""]) {
    htr.appendChild(el("th", h === "Note" || h === "Valable dès le" ? "" : "num", h));
  }
  thead.appendChild(htr);
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const t of tarifs) {
    const tr = el("tr");
    tr.appendChild(el("td", null, fmtDay(Date.parse(t.valid_from) / 1000)));
    tr.appendChild(el("td", "num", `${fmtCHF(t.prix_reseau)} / kWh`));
    tr.appendChild(el("td", "num", `${fmtCHF(t.prix_solaire)} / kWh`));
    tr.appendChild(el("td", "num", `${t.taux_tva} %`));
    tr.appendChild(el("td", null, t.note || "—"));

    const actions = el("td");
    const btn = el("button", "btn-link", "Supprimer");
    btn.type = "button";
    btn.addEventListener("click", () => onDelete(t));
    actions.appendChild(btn);
    tr.appendChild(actions);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
}

export function initTarifs({ table, form, message, tarifs, onChange }) {
  let current = tarifs;

  const handleDelete = async (t) => {
    if (!window.confirm(
      `Supprimer le tarif valable dès le ${t.valid_from} ?\n` +
      "Les mois qui s'appuyaient dessus seront recalculés avec le tarif précédent."
    )) return;
    current = await deleteTarif(t.id);
    renderTable(table, current, handleDelete);
    message.textContent = "Tarif supprimé, décompte recalculé.";
    onChange();
  };

  renderTable(table, current, handleDelete);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    message.textContent = "";
    try {
      current = await saveTarif({
        valid_from: data.valid_from,
        prix_reseau: Number(data.prix_reseau),
        prix_solaire: Number(data.prix_solaire),
        taux_tva: Number(data.taux_tva),
        note: data.note || "",
      });
      renderTable(table, current, handleDelete);
      message.textContent = "Tarif enregistré, décompte recalculé.";
      onChange();
    } catch (err) {
      message.textContent = `Échec de l'enregistrement : ${err.message}`;
    }
  });
}
