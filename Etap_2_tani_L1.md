# Etap 2 — tani, skuteczny PL:L1

Stan: pierwsza wersja produkcyjna `cheap-effective-v2`.

## Cel

Pierwsze nasycenie katalogu ma dostarczać użyteczne, audytowalne dane PL:L1
bez płacenia za pełny research każdej marki. Wynik nadal przechodzi Human
Review i Publication Gate. Pipeline nie publikuje automatycznie ani wartości
Gold, ani odpowiedzi modelu.

## Przebieg

1. `website_url` z katalogu jest przekazywany jako `unverified_seed` (albo
   `validated_official`, jeżeli został wcześniej zweryfikowany).
2. Planner PL:L1 działa deterministycznie i bez OpenAI.
3. Searcher offline materializuje domenę oraz typowe ścieżki:
   `/franczyza/`, `/franchise/`, `/o-nas/`, `/kontakt/`.
4. Extractor pobiera te strony przed web search, korzysta ze współdzielonego
   cache canonical URL/content i grupuje zakresy zadań przypisane do jednego
   dokumentu w jednym wywołaniu semantycznym.
5. Risk-based Checker semantycznie kontroluje przede wszystkim tożsamość,
   strony, wartości finansowe i skalę sieci. Pole niskiego ryzyka pozostaje
   jawnie `not_reviewed`; jego dokładnie ugruntowana cytatem wartość trafia do
   Workbencha jako `grounded_unreviewed`, nigdy jako automatycznie zatwierdzona.
6. Gdy oficjalne źródła dają co najmniej 8 zaakceptowanych pól, Searcher jest
   pomijany.
7. Gdy pozostają braki, deterministyczny Resolver wybiera brakujące zadania.
   Dopiero wtedy Executor uruchamia płatnego Searchera i Extractora oraz scala
   wynik z dokładną linią poprzednich artefaktów.
8. Normalizer przygotowuje draft. Launcher kończy jako `complete`, `partial`
   albo `insufficient`; status `succeeded` nie maskuje niepełnego L1.

## Kontrola kosztu i wznowienia

- cache dokumentów: `datacollector/data/document_cache`;
- limit Searchera, źródeł, Extractora i budżetu nadal pochodzi z formularza;
- koszt jest sprawdzany między płatnymi etapami;
- run przechowuje osobno artefakty seedów, seed Checkera, Resolvera i Executora;
- po błędzie wznowienie używa istniejących artefaktów i nie powtarza
  zakończonych płatnych etapów;
- ekran runu pokazuje pełny ślad obu faz.

## Gold → Workbench

Gold pozostaje niezależnym artefaktem benchmarkowym. Mechanizm promocji:

- wymaga istniejącego, niezakończonego Workbencha PL:L1 tej samej marki;
- pokazuje podgląd wszystkich 20 pól, konfliktów i miejsca publikacji;
- puste pola uzupełnia jako oczekujące propozycje;
- istniejącą propozycję pipeline'u zachowuje, a Gold dodaje jako osobną
  referencję porównawczą;
- `not_public` i `not_applicable` stają się sugestiami braków, a nie decyzją;
- zapisuje SHA-256 Gold, URL, typ źródła, daty i proweniencję
  `benchmark_gold_ai_proxy`;
- nie zmienia decyzji człowieka, nie dotyka zamrożonego Workbencha i niczego
  automatycznie nie publikuje.

## Bramki pilotażowe

Po kolejnej kohorcie należy mierzyć:

- co najmniej 8–12 propozycji pól na markę;
- co najmniej 60% wartości zaakceptowanych bez edycji;
- Human Review nie dłuższy niż 10–15 minut;
- minimum 10 zaakceptowanych pól na 1 USD znanego kosztu;
- 100% publicznych liczb z typem źródła i datą pobrania/ważności;
- udział marek, dla których Searcher został całkowicie pominięty;
- cache hit rate oraz koszt fazy official-only i gap-search osobno.

Wyniki należy porównywać z Gold Setem i eksperymentem researcher + ChatGPT.
Są to bramki pilotażowe, nie gwarancja jakości dla każdego rodzaju franczyzy.
