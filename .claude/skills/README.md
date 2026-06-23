# analizbot — Claude Skills

Bu klasör, analizbot'u Claude Code/Cowork içinden **sohbetle** kullanmak için Claude
Agent Skill'leri içerir. İki kaynaktan esinlenmiştir:
- **tradermonty/claude-trading-skills** — trading skill formatı + metodolojiler.
- **anthropics/financial-services** — çok-ajan orkestrasyon + rapor desenleri.

Skill'ler iki gruba ayrılır:

## 1) Botu saran skiller (deterministik CLI'yi çağırır)
| Skill | Ne yapar | Çağırdığı komut |
|---|---|---|
| `crypto-analyze` | Tek sembol BUY/SELL/HOLD analizi | `analyze` |
| `market-screen` | Piyasayı tarar, fırsatları sıralar | `screen` |
| `regime-check` | Piyasa rejimi (RISK_ON/OFF) | `regime` |
| `backtest-runner` | Backtest + walk-forward/overfit | `backtest`, `optimize` |

## 2) LLM-native skiller (kodun yapamadığı niteliksel analiz)
| Skill | Ne yapar | İlham |
|---|---|---|
| `crypto-news-impact` | Haber etki skorlaması | market-news-analyst |
| `scenario-analyzer` | Baz/Boğa/Ayı senaryo + eleştiri | scenario-analyzer |
| `theme-detector` | Trend anlatı/sektör tespiti | theme-detector |
| `narrative-report` | Sayısal + niteliksel → rapor | financial-services |

## 3) Orkestrasyon
| Skill | Ne yapar |
|---|---|
| `daily-workflow` | rejim → tarama → analiz → haber → rapor zinciri |

## Kullanım
Claude Code'da proje kökünde sohbet ederken doğal dille tetiklenir:
- "BTC'yi analiz et" → `crypto-analyze`
- "piyasa risk-on mu?" → `regime-check`
- "günlük rutini çalıştır" → `daily-workflow`

Botu doğrudan da çalıştırabilirsiniz:
```
.venv\Scripts\python.exe -m src.app.cli <komut> ...
```

**Uyarı:** Bu skill'ler **read-only** analizdir (yatırım tavsiyesi değildir). Otonom işlem
ayrı ve opsiyoneldir; yalnız `watch --execute` / `trade` ile, açıkça etkinleştirilince devreye
girer (varsayılan: paper/simülasyon, canlı için üçlü kilit).
