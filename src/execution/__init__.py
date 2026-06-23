"""Otonom işlem (emir yürütme) modülü.

GÜVENLİK ÖNCELİKLİ: Bu modül GERÇEK emir gönderebilir. Üç kademe vardır —
`paper` (simülasyon, API yok, VARSAYILAN), `testnet` (Binance testnet, sahte para)
ve `live` (gerçek para). Canlı emir yalnız ÜÇLÜ KİLİT açıkken gönderilir
(bkz. `src.execution.factory.build_executor`).

MİMARİ KURAL: Çekirdek/strateji bu modülü import ETMEZ; yalnız watch döngüsü
(`src.app.scheduler`) kullanır. `OrderExecutor` ABC sayesinde paper/testnet/live
ve ileride futures aynı arayüzle eklenir.
"""
