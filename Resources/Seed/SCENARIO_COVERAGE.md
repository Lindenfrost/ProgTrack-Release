# ProgTrack 0.2.1 fictional seed scenario coverage

The package is entirely fictional and is the single clean initialization
source for Standalone SQLite and Shared PostgreSQL development/demo systems.

| Scenario | Coverage |
|---|---|
| Complete immutable identity | Every animal; four-block IPID including origin |
| Projects | Backcrossing, OTOF-, Oakshield, Ringbearer and Shire; both mouse projects use Mus musculus and canonical organizational Units |
| Weight history | Every active and archived animal through its latest researcher-entered scientific/clinical record |
| Species | Callitrix, Macaca, Papio, Mus |
| Mus musculus | Canonical Hobbit-derived genealogy (Drogo + Primula -> Frodo), Italian partner names, realistic mouse weights and connected ancestry |
| Ringbearer | Frodo/Sam and Merry/Pippin are adult experimental mice in one project and two pair cages; each has one surgery event and no birth event |
| Shire | Eight founder pairs; five planned offspring generations; 1-2 litters per pair, 10-12 young per litter, at most two selected per pair across all litters; complete mouse seed below 500. Deterministic pedigree-coancestry minimization reduces inbreeding; a closed colony is not claimed to be inbreeding-free. Existing parents and identities are retained. |
| Shire exception and provenance | Sam remains Elanor's father; a complete Sam/Rosie litter adds generation 6. Ringbearers retain their current project and procedures, with former Shire membership in Core/project history. Machine-readable litter, generation, selection and pairing evidence: seed/shire-colony domain record. Existing unrelated legacy mouse families remain background examples, not new selection rounds. |
| Shire names and users | Hobbit names; ASCII Portuguese names for newly invented spouses; deterministic numeric suffixes distinguish repeated family names. Researcher PI/planning, Manager applicant, Vet deputy/clinical, Veti welfare (Vet + AWO), Keeper husbandry; no extra privilege overrides or duplicate users. |
| Heritage shipped seed | No Heritage-owned dummy animals are packaged; runtime dummy support is exercised only through explicit fixtures |
| Mouse House | Dedicated building/unit/room; two Ringbearer pair cages, separate ordinary breeding-pair cages and same-sex groups of at most five, plus Bilbo's deliberate single cage |
| Denethor family | Elros descendant; Boromir and Faramir with distinct donors/surrogates |
| OTOF- success | Two complete transfer, pregnancy-verification, and live-birth paths |
| OTOF- failure | Poor yield, low embryo development, repeated negative pregnancy verification, two verified pregnancies followed by abortions |
| Monitoring | Three 28-day cycles; 10–11 day follicular phase; complete FSH/retrieval and PGF chains |
| Sperm collection | One donation event per sample ID, including Denethor's OTOF donation |
| Users/roles | Canonical Admin, Researcher, Vet, Manager, Keeper, Tester, and Veti accounts; Veti is a user with Vet + AWO jobs (AWO requires Vet); password `123456` |
| Backend parity | One canonical interchange package for both adapters |
