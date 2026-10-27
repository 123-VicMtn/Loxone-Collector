"""
Lanceur de l'API, gardé à cet emplacement pour que `python app.py
config.yaml` (depuis backend/) et le service systemd continuent de
fonctionner. Le code vit dans src/loxone_collector/app.py.
"""
from loxone_collector.app import main

if __name__ == "__main__":
    main()
