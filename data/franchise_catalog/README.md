# Katalog franczyz PL

`pl_franchises_2026-07-22.json` i odpowiadający mu CSV są audytowalnym
snapshotem leadów rynkowych na 2026-07-22. Nie istnieje kompletny publiczny,
urzędowy rejestr franczyz w Polsce, dlatego `listed` oznacza obecność oferty w
bieżącym katalogu branżowym, a nie niezależne potwierdzenie aktywności.

Źródła bazowe:

- `https://franchising.pl/katalog/wszystkie/`;
- `https://franczyzawpolsce.pl/baza-sieci/`;
- jawnie opisane uzupełnienia istniejących realnych sieci jako `uncertain`;
- niezależne materiały o zamknięciu North Fish.

Pliki odtwarza `scripts/build_pl_franchise_catalog.py` z zapisanych snapshotów
HTML. Skrypt nie kopiuje finansów ani pełnych opisów wydawców.

Synchronizacja Django jest domyślnie dry-runem:

```bash
.venv/bin/python src/saashome/manage.py sync_pl_franchise_catalog \
  --prune-unresearched --prune-operational-placeholders
```

Zapis wymaga dwóch jawnych flag:

```bash
.venv/bin/python src/saashome/manage.py sync_pl_franchise_catalog \
  --apply --prune-unresearched --prune-operational-placeholders \
  --confirm-prune-unresearched
```

Profile posiadające import, Workbench, launch lub opublikowane pola researchu
nie są usuwane. Przed usunięciem powstaje lokalny backup podstawowych rekordów.
