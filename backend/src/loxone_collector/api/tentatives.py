"""
Limite des tentatives de connexion : au-delà de `MAX_ECHECS` échecs en
`FENETRE_S` secondes, par adresse IP ou par identifiant, `POST /api/login`
répond 429 jusqu'à ce que le plus ancien échec sorte de la fenêtre, même
avec le bon mot de passe.

En mémoire : l'API tourne dans un seul process (serve.py), un compteur
partagé entre ses threads suffit. Un redémarrage remet les compteurs à zéro.
L'IP est celle du client réel derrière Caddy (waitress lit X-Forwarded-For
quand `trusted_proxy` est configuré).
"""

from __future__ import annotations

import threading
import time
from collections import deque

MAX_ECHECS = 5
FENETRE_S = 15 * 60


class LimiteurConnexion:
    def __init__(self, max_echecs: int = MAX_ECHECS, fenetre_s: int = FENETRE_S,
                 horloge=time.monotonic):
        self.max_echecs = max_echecs
        self.fenetre_s = fenetre_s
        self._horloge = horloge
        self._echecs: dict[tuple[str, str], deque] = {}
        self._verrou = threading.Lock()

    def _recents(self, cle: tuple[str, str], maintenant: float) -> deque:
        """Échecs encore dans la fenêtre ; une clé vidée est oubliée (la
        mémoire ne grossit pas avec les adresses passées)."""
        echecs = self._echecs.get(cle)
        if echecs is None:
            return deque()
        while echecs and echecs[0] <= maintenant - self.fenetre_s:
            echecs.popleft()
        if not echecs:
            del self._echecs[cle]
        return echecs

    def attente(self, cles: list[tuple[str, str]]) -> int:
        """Secondes avant une nouvelle tentative, 0 si elle est permise."""
        with self._verrou:
            maintenant = self._horloge()
            attente = 0.0
            for cle in cles:
                echecs = self._recents(cle, maintenant)
                if len(echecs) >= self.max_echecs:
                    attente = max(attente, echecs[0] + self.fenetre_s - maintenant)
            return int(attente) + 1 if attente else 0

    def echec(self, cles: list[tuple[str, str]]) -> None:
        with self._verrou:
            maintenant = self._horloge()
            for cle in cles:
                self._recents(cle, maintenant)
                self._echecs.setdefault(cle, deque()).append(maintenant)

    def reussite(self, cles: list[tuple[str, str]]) -> None:
        with self._verrou:
            for cle in cles:
                self._echecs.pop(cle, None)

    def vider(self) -> None:
        with self._verrou:
            self._echecs.clear()


limiteur = LimiteurConnexion()


def cles(ip: str | None, username: str) -> list[tuple[str, str]]:
    return [("ip", ip or "?"), ("identifiant", username.lower())]
