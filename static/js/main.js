/**
 * Point d'entrée du dashboard (templates/index.html). Charge les modules
 * ES natifs (pas de bundler -- déploiement direct sur Raspberry Pi) et
 * câble les onglets : l'Explorer est actif dès le chargement, Énergie et
 * Consommations par zone s'initialisent paresseusement à leur premier
 * affichage (voir tabs.js).
 *
 * Pour ajouter un nouvel onglet : créer un module sous tabs/, l'importer
 * ici, l'ajouter à la map passée à setupTabs().
 */

import { setupTabs } from "./tabs.js";
import { initExplorerTab } from "./tabs/explorer-tab.js";
import { initEnergyTab } from "./tabs/energy-tab.js";
import { initZoneTab } from "./tabs/zone-tab.js";
import { initHealthFooter } from "./core/health.js";

document.addEventListener("DOMContentLoaded", () => {
  initExplorerTab();
  setupTabs({ energie: initEnergyTab, zone: initZoneTab });
  initHealthFooter();
});
