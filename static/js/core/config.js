/**
 * Accès aux quelques valeurs injectées par le template Jinja dans
 * `window.*` (voir templates/index.html). Centraliser ces lectures ici
 * évite que chaque module aille piocher directement dans `window` --
 * si un jour ces données viennent d'un appel API plutôt que d'un
 * `<script>` inline, un seul fichier change.
 */

export function getResourceTypeLabels() {
  return window.RESOURCE_TYPE_LABELS || {};
}
