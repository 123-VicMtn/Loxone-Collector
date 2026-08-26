/**
 * Bascule générique entre onglets (`.tab-btn` / `.tab-panel`). Ne connaît
 * rien du contenu de chaque onglet : `onActivate` est une map optionnelle
 * `{ [data-tab]: callback }` appelée la première fois qu'un onglet est
 * ouvert (init paresseuse -- évite de charger les données de tous les
 * onglets au chargement de la page).
 *
 * Pour ajouter un nouvel onglet : un bouton + un panel dans le template,
 * et une entrée dans la map passée à setupTabs() si l'onglet a besoin
 * d'une init au premier affichage. Rien à changer ici.
 */

export function setupTabs(onActivate = {}) {
  const buttons = document.querySelectorAll(".tab-btn");
  const panels = {};
  buttons.forEach((btn) => {
    const key = btn.dataset.tab;
    panels[key] = document.getElementById(`tab-${key}`);
  });

  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.tab;

      buttons.forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");

      Object.entries(panels).forEach(([panelKey, el]) => {
        if (el) el.hidden = panelKey !== key;
      });

      if (typeof onActivate[key] === "function") onActivate[key]();
    });
  });
}
